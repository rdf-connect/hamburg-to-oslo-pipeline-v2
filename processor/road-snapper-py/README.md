# Road Snapper (Python, RDF-Connect)

A lean RDF-Connect processor that snaps each SensorThings measurement point to
the nearest drivable road in an OpenStreetMap extract and **injects the resulting
road segment back into the JSON record**, so the downstream RML mapping can build
an OSLO `Wegsegment` from it.

It is a **pre-RML JSON enricher**: it reads the raw SensorThings JSON (before any
RDF exists), adds a `_wegsegment` field, and passes the record on unchanged
otherwise. It does **not** produce or read RDF.

This is a **reimplementation of the essential snap logic** from the
`gader-data-onboarding` road-snapper (Clojure/Kafka Streams). It deliberately
drops everything that only existed for Kafka-scale streaming — H3 spatial cache,
RocksDB state store, repartition topics, the HTTP API — and keeps just the domain
core: load roads into an R-tree, find the nearest segment, expose its centerline
and start/end. Language is Python to match the pipeline's other Python processors.

## Why it exists

The Hamburg SensorThings source gives each measurement point a **single
coordinate** and a free-text direction (e.g. `"Nord nach Süd"`). The OSLO
Verkeersmetingen standard wants a `Wegsegment` and a `Rijrichting` relative to it.
There is no road/segment information in the source, so it must be **derived** by
snapping the point to a road network. This processor produces that segment; the
downstream [direction-mapper](../direction-mapper-py) then classifies the
measured direction relative to the segment's bearing.

## Position in the pipeline

The two enrichers run **before** the RML mapper, on the SensorThings JSON:

```
fetcher → <json> → roadSnapper → <snappedJson> → directionMapper → <enrichedJson> → mapper(RML) → translator → validator → poster
```

## What it injects

For each record whose `locations[0].location.geometry.coordinates` snaps to a road
within `maxDistanceMeters`, it adds a single `_wegsegment` key. Every original
field is preserved.

```jsonc
"_wegsegment": {
  "centerlineWkt": "LINESTRING (...)",  // the snapped road segment
  "beginWkt":      "POINT (...)",       // segment start node
  "eindWkt":       "POINT (...)",       // segment end node
  "offsetM":       94.408,               // distance along the segment to the snapped point
  "distanceM":     3.2                   // perpendicular distance from the point to the segment
}
```

Records with no coordinate, or with no road within `maxDistanceMeters`, are
passed through **unchanged** (no `_wegsegment` key) and a warning is logged.

## Configuration (`processor.ttl`)

| Property | Required | Default | Description |
|---|---|---|---|
| `rdfc:reader` | yes | — | incoming SensorThings JSON stream |
| `rdfc:writer` | yes | — | outgoing (enriched) JSON stream |
| `rdfc:sourcePath` | yes | — | path to an OSM roads `.shp` **or** an `.osm.pbf` extract |
| `rdfc:projectedCrs` | no | `EPSG:25832` | metric CRS for distance math. UTM 32N is correct for Hamburg — **not** Belgian Lambert72 (the gader default) |
| `rdfc:maxDistanceMeters` | no | `500.0` | reject the snap if the nearest segment is farther than this |
| `rdfc:layer` | no | `lines` | OGR layer to read (roads live in `lines` in an `.osm.pbf`) |
| `rdfc:where` | no | drivable-highway filter | OGR SQL filter, pushed down to GDAL. Empty → the built-in drivable-`highway` filter |

Optional properties absent from the pipeline `.ttl` fall back to the defaults
above.

## The road-network data (the one thing you must supply)

The processor needs an **OSM road network covering Hamburg**. It reads either a
roads shapefile *or* an `.osm.pbf` extract directly (via GDAL/pyogrio) — no
conversion step needed. The gader original ships BeNeLux shapefiles in Belgian
Lambert72, which is wrong for Germany; this processor defaults to UTM 32N.

The tested setup uses a Geofabrik **Hamburg-region `.osm.pbf`** placed at
`resources/hamburg-<date>.osm.pbf`:

```bash
# Dated snapshot for reproducibility (not -latest)
curl -LO https://download.geofabrik.de/europe/germany/hamburg-260714.osm.pbf
mv hamburg-260714.osm.pbf resources/
```

This file is **not** committed (see `.gitignore`) — supply it locally.

### Road class filter (important)

In a `.pbf` the `lines` layer mixes roads with **footways, cycleways, waterways,
and railways**. On raw nearest-distance, a footpath often sits a couple of metres
closer to a sensor than the road it counts — so without a filter, motor-vehicle
counters mis-snap to footways. Verified on the three live Hamburg meetpunten: with
**no** filter, 2 of 3 snapped to unnamed footways; with the drivable filter, all 3
snapped to their real named streets.

The default `where` therefore keeps only **drivable** `highway` classes
(`motorway`/`trunk`/`primary`/`secondary`/`tertiary` + links, `unclassified`,
`residential`, `living_street`, `service`). Override via `rdfc:where` if needed.

> A pre-filtered roads shapefile may not have a `highway` column; the loader
> detects the resulting filter error and retries without the `where` clause.

## Dependencies

- **shapely** — WKT parsing, `STRtree` R-tree nearest-neighbour, `LineString.project()` linear referencing
- **pyproj** — WGS84 ↔ metric CRS reprojection (metre-accurate distances)
- **pyogrio** — GDAL-backed reader for shapefiles and `.osm.pbf`
- **geopandas** — required by `pyogrio.read_dataframe` for the geometry frame

## Testing

`tests/test_snap.py` builds a synthetic road network and snaps the real meetpunt
coordinate from the pipeline — no external OSM data needed:

```bash
python3 tests/test_snap.py
```

## Building for the pipeline

Like the other bundled Python processors, build an sdist the pipeline can
reference:

```bash
cd processor/road-snapper-py
hatch build -t sdist   # -> dist/rdfc_road_snapper-0.0.1.tar.gz
```

The `dist/` output is build-only and git-ignored. Reference the sdist from the
pipeline's `pyproject.toml` under `[tool.uv.sources]`, then `hatch env create` in
`pipeline/` to install it.
