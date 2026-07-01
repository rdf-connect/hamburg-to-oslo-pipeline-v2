import asyncio
from dataclasses import dataclass
from logging import getLogger, Logger

from rdflib import Graph, Literal
from transformers import pipeline

from rdfc_runner import Processor, Reader, Writer


# --- Type Definitions ---
@dataclass
class TranslationArgs:
    reader: Reader
    writer: Writer
    model: str
    source_language: str
    target_language: str


# --- Processor Implementation ---
class TranslationProcessor(Processor[TranslationArgs]):
    logger: Logger = getLogger("rdfc.TranslationProcessor")

    def __init__(self, args: TranslationArgs):
        super().__init__(args)

        self.translator = None

        # Cache persists for the entire lifetime of the processor.
        self.translation_cache: dict[str, str] = {}

        # Serialize writer operations.
        # This avoids overlapping gRPC writes on the same stream.
        self._writer_lock = asyncio.Lock()
        self._writer_closed = False

        self.logger.debug(
            f"Created TranslationProcessor with args: {args}"
        )

    def _normalize_cache_key(self, text: str) -> str:
        """
        Normalize text to maximize cache hits.
        """
        return " ".join(text.strip().split())

    async def init(self) -> None:
        self.logger.debug(
            f"Initializing TranslationProcessor with args: {self.args}"
        )

        # transformers.pipeline() is synchronous, but init runs only once.
        self.translator = pipeline(
            task="translation",
            model=self.args.model,
        )

    def _translate_sync(self, texts: list[str]) -> list[str]:
        """
        Synchronous translation helper.

        This is called through asyncio.to_thread(...) so that the blocking
        HuggingFace pipeline call does not block the RDF-Connect/gRPC event loop.
        """
        results = self.translator(
            texts
        )

        return [
            result["translation_text"]
            for result in results
        ]

    async def _safe_write_string(self, data: str) -> None:
        """
        Write to the RDF-Connect writer while guaranteeing that only one writer
        operation is active at a time.
        """
        async with self._writer_lock:
            if self._writer_closed:
                self.logger.warning(
                    "Attempted to write after writer was closed; skipping."
                )
                return

            await self.args.writer.string(data)

    async def _safe_close_writer(self) -> None:
        """
        Close the RDF-Connect writer exactly once.

        This should only be called after the upstream reader has ended.
        """
        async with self._writer_lock:
            if not self._writer_closed:
                self._writer_closed = True
                await self.args.writer.close()

    async def transform(self) -> None:
        total_cache_hits = 0
        total_cache_misses = 0
        message_count = 0

        async for data in self.args.reader.strings():
            message_count += 1

            self.logger.debug(
                f"Received RDF message {message_count}"
            )

            g = Graph()
            g.parse(data=data, format="turtle")

            candidates: list[tuple] = []
            unseen_texts: set[str] = set()

            # Collect literals that should be translated.
            for s, p, o in g:
                if (
                    isinstance(o, Literal)
                    and o.language == self.args.source_language
                ):
                    original_text = str(o)
                    cache_key = self._normalize_cache_key(original_text)

                    candidates.append((s, p, cache_key))

                    if cache_key in self.translation_cache:
                        total_cache_hits += 1
                    else:
                        total_cache_misses += 1
                        unseen_texts.add(cache_key)

            # Translate only texts that are not yet in the cache.
            if unseen_texts:
                texts = list(unseen_texts)

                self.logger.info(
                    f"Message {message_count}: translating "
                    f"{len(texts)} unique text(s)"
                )

                translated_texts = await asyncio.to_thread(
                    self._translate_sync,
                    texts,
                )

                for text, translated_text in zip(texts, translated_texts):
                    self.translation_cache[text] = translated_text

            # Add translated literals to the graph.
            for s, p, cache_key in candidates:
                translated_text = self.translation_cache[cache_key]

                g.add(
                    (
                        s,
                        p,
                        Literal(
                            translated_text,
                            lang=self.args.target_language,
                        ),
                    )
                )

            serialized = g.serialize(format="turtle")

            await self._safe_write_string(serialized)

            self.logger.info(
                f"Translation done for message {message_count}. "
                f"Total cache hits={total_cache_hits}, "
                f"misses={total_cache_misses}, "
                f"cached translations={len(self.translation_cache)}"
            )

        # Important:
        # We only reach this point when the upstream reader has ended/closed.
        # Therefore it is now safe to close the downstream writer.
        await self._safe_close_writer()

        self.logger.info(
            f"Translation processor input stream closed. "
            f"Output writer closed. "
            f"Messages={message_count}, "
            f"cache hits={total_cache_hits}, "
            f"misses={total_cache_misses}, "
            f"cached translations={len(self.translation_cache)}"
        )

    async def produce(self) -> None:
        pass