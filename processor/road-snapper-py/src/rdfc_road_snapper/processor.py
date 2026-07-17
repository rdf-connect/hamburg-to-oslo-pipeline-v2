import asyncio
import json
from dataclasses import dataclass
from logging import getLogger, Logger

from rdfc_runner import Processor, Reader, Writer

from .snap import RoadSnapper, SnapResult


# --- Type Definitions ---
@dataclass
class RoadSnapperArgs:
    reader: Reader
    writer: Writer
    source_path: str
    projected_crs: str = "EPSG:25832"
    max_distance_meters: float = 500.0
    layer: str = "lines"
    where: str | None = None  # None -> RoadSnapper's drivable-highway default


# --- Processor Implementation ---
class RoadSnapperProcessor(Processor[RoadSnapperArgs]):
    """Pre-RML JSON enricher.

    Reads the SensorThings JSON records emitted by the fetcher, snaps each
    record's measurement coordinate to the nearest drivable road segment, and
    injects a ``_wegsegment`` object into the record so the RML mapping can map
    the Wegsegment (centerline + begin/end knoop) directly. Purely additive:
    every original field is preserved.

    Injected shape (per record):
        record["_wegsegment"] = {
            "centerlineWkt": "LINESTRING (...)",
            "beginWkt":      "POINT (...)",
            "eindWkt":       "POINT (...)",
            "offsetM":       <float>,
            "distanceM":     <float>,
        }
    Records with no coordinate, or no segment within max distance, are passed
    through unchanged (no ``_wegsegment`` key).
    """

    logger: Logger = getLogger("rdfc.RoadSnapperProcessor")

    def __init__(self, args: RoadSnapperArgs):
        super().__init__(args)
        self.snapper: RoadSnapper | None = None
        self._writer_lock = asyncio.Lock()
        self._writer_closed = False
        self.logger.debug(f"Created RoadSnapperProcessor with args: {args}")

    async def init(self) -> None:
        self.logger.debug(
            f"Initializing RoadSnapperProcessor with args: {self.args}"
        )
        from .snap import DRIVABLE_HIGHWAY, _drivable_where

        # Optional TTL properties arrive as None when omitted (the runner does
        # not apply dataclass defaults), so coalesce each to its default here.
        projected_crs = self.args.projected_crs or "EPSG:25832"
        max_distance = (
            500.0
            if self.args.max_distance_meters is None
            else float(self.args.max_distance_meters)
        )
        layer = self.args.layer or "lines"
        where = self.args.where or _drivable_where(DRIVABLE_HIGHWAY)

        # Loading + indexing the road network is blocking; run off the event loop.
        self.snapper = await asyncio.to_thread(
            RoadSnapper,
            self.args.source_path,
            projected_crs,
            max_distance,
            layer,
            where,
        )
        self.max_distance_meters = max_distance
        self.logger.info(
            f"Road network loaded from {self.args.source_path} "
            f"(layer={layer}, projected CRS {projected_crs}, "
            f"max distance {max_distance} m)"
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
    def _point_wkt_from_record(record: dict) -> str | None:
        """Build a WKT POINT from the SensorThings location coordinate.

        Path: locations[0].location.geometry.coordinates = [lon, lat].
        """
        try:
            coords = record["locations"][0]["location"]["geometry"]["coordinates"]
            lon, lat = float(coords[0]), float(coords[1])
        except (KeyError, IndexError, TypeError, ValueError):
            return None
        return f"POINT ({lon} {lat})"

    def _enrich_record(self, record: dict) -> bool:
        """Snap the record's coordinate and inject ``_wegsegment``.

        Returns True if a segment was injected."""
        point_wkt = self._point_wkt_from_record(record)
        if point_wkt is None:
            self.logger.warning("Record has no usable coordinate; passing through.")
            return False

        try:
            result: SnapResult | None = self.snapper.snap(point_wkt)
        except ValueError as exc:
            self.logger.warning(f"Snap failed ({exc}); passing through.")
            return False

        if result is None:
            self.logger.warning(
                f"No road segment within {self.max_distance_meters} m of "
                f"{point_wkt}; passing through unsnapped."
            )
            return False

        record["_wegsegment"] = {
            "centerlineWkt": result.road_segment_wkt,
            "beginWkt": result.road_segment_start_wkt,
            "eindWkt": result.road_segment_end_wkt,
            "offsetM": result.offset_m,
            "distanceM": result.distance_m,
        }
        return True

    async def transform(self) -> None:
        message_count = 0
        total_snapped = 0

        async for data in self.args.reader.strings():
            message_count += 1
            record = json.loads(data)

            snapped = await asyncio.to_thread(self._enrich_record, record)
            if snapped:
                total_snapped += 1

            await self._safe_write_string(json.dumps(record))
            self.logger.info(
                f"Road-snap message {message_count}: "
                f"{'snapped' if snapped else 'passed through'} "
                f"(total snapped={total_snapped})."
            )

        await self._safe_close_writer()
        self.logger.info(
            f"Road snapper input stream closed. Output writer closed. "
            f"Messages={message_count}, snapped={total_snapped}."
        )

    async def produce(self) -> None:
        pass
