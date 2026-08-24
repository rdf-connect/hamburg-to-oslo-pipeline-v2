import asyncio
import json
from dataclasses import dataclass
from logging import getLogger, Logger

from rdfc_runner import Processor, Reader, Writer

from .classify import (
    DirectionLabel,
    DirectionParseError,
    classify_direction,
    parse_german_direction,
)


# --- Type Definitions ---
@dataclass
class DirectionMapperArgs:
    reader: Reader
    writer: Writer
    threshold: float = 90.0


# --- Processor Implementation ---
class DirectionMapperProcessor(Processor[DirectionMapperArgs]):
    """Pre-RML JSON enricher.

    Reads the SensorThings JSON records (already enriched with ``_wegsegment``
    by the road-snapper), classifies the German direction phrase
    (``thing.properties.richtung``) into an INSPIRE LinkDirectionValue code
    relative to the snapped segment's start->end bearing, and injects a
    ``_rijrichting`` object.

    Two-level direction (per the OSLO reference model):
      * ``locationDirection``    — the direction(s) the meetpunt measures. For
        Hamburg each datastream is a single traversal direction, so this equals
        the measurement direction (never bothDirections unless the phrase is
        "Keine Richtung").
      * ``measurementDirection`` — the actual direction of this count. Same
        value for Hamburg (one direction per datastream).

    Injected shape (per record):
        record["_rijrichting"] = {
            "locationDirection":    "inDirection" | "inOppositeDirection" | "bothDirections",
            "measurementDirection": "inDirection" | "inOppositeDirection" | "bothDirections",
        }
    Records that cannot be classified (no phrase, unparseable, or no
    ``_wegsegment`` for a directional phrase) are passed through unchanged.
    """

    logger: Logger = getLogger("rdfc.DirectionMapperProcessor")

    def __init__(self, args: DirectionMapperArgs):
        super().__init__(args)
        self._writer_lock = asyncio.Lock()
        self._writer_closed = False
        self.logger.debug(f"Created DirectionMapperProcessor with args: {args}")

    async def init(self) -> None:
        self.logger.debug(
            f"Initializing DirectionMapperProcessor with args: {self.args}"
        )

    async def _safe_write_string(self, data: str) -> None:
        async with self._writer_lock:
            if self._writer_closed:
                self.logger.warning(
                    "Attempted to write after writer was closed; skipping."
                )
                return
            await self.args.writer.string(data)

    async def _safe_close_writer(self) -> None:
        async with self._writer_lock:
            if not self._writer_closed:
                self._writer_closed = True
                await self.args.writer.close()

    @staticmethod
    def _phrase_from_record(record: dict) -> str | None:
        try:
            return record["thing"]["properties"]["richtung"]
        except (KeyError, TypeError):
            return None

    def _classify_record(self, record: dict) -> DirectionLabel | None:
        phrase = self._phrase_from_record(record)
        if phrase is None:
            self.logger.warning("Record has no richtung phrase; passing through.")
            return None

        try:
            azimuth = parse_german_direction(phrase)
        except DirectionParseError as exc:
            self.logger.warning(f"{exc}; passing through.")
            return None

        # "Keine Richtung" -> bothDirections, no segment needed.
        if azimuth is None:
            return DirectionLabel.BOTH_DIRECTIONS

        seg = record.get("_wegsegment")
        if not seg or "beginWkt" not in seg or "eindWkt" not in seg:
            self.logger.warning(
                f"Directional phrase {phrase!r} but no _wegsegment injected; "
                f"cannot classify in/opposite. Passing through."
            )
            return None

        # threshold arrives as None when the optional TTL property is omitted.
        threshold = 90.0 if self.args.threshold is None else float(self.args.threshold)
        return classify_direction(
            azimuth,
            seg["beginWkt"],
            seg["eindWkt"],
            threshold=threshold,
        )

    def _enrich_record(self, record: dict) -> bool:
        label = self._classify_record(record)
        if label is None:
            return False
        # Hamburg: one direction per datastream, so location == measurement.
        record["_rijrichting"] = {
            "locationDirection": label.value,
            "measurementDirection": label.value,
        }
        return True

    async def transform(self) -> None:
        message_count = 0
        total_mapped = 0

        async for data in self.args.reader.strings():
            message_count += 1
            record = json.loads(data)

            mapped = await asyncio.to_thread(self._enrich_record, record)
            if mapped:
                total_mapped += 1

            await self._safe_write_string(json.dumps(record))
            self.logger.info(
                f"Direction-map message {message_count}: "
                f"{'classified' if mapped else 'passed through'} "
                f"(total classified={total_mapped})."
            )

        await self._safe_close_writer()
        self.logger.info(
            f"Direction mapper input stream closed. Output writer closed. "
            f"Messages={message_count}, classified={total_mapped}."
        )

    async def produce(self) -> None:
        pass
