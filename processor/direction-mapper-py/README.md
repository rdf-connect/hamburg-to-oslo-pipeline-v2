# Direction Mapper (Python, RDF-Connect)

An RDF-Connect processor that classifies each Hamburg record's German direction
phrase (e.g. `"Nord nach Süd"`) as one of the OSLO / INSPIRE `LinkDirectionValue`
codes — `inDirection`, `inOppositeDirection`, or `bothDirections` — relative to
the road segment the point was snapped onto, and **injects the result back into
the JSON record**.

Like the [road-snapper](../road-snapper-py), it is a **pre-RML JSON enricher**: it
reads SensorThings JSON (already carrying the `_wegsegment` field the road-snapper
added), adds a `_rijrichting` field, and forwards the record. It does **not**
produce or read RDF.

The classification math (`classify_direction`, azimuth, angle-diff, and the
`DirectionLabel` enum) is **ported verbatim** from the `gader-data-onboarding`
direction-mapper. The only Hamburg-specific addition is a parser for German
traversal phrases (`"X nach Y"`), replacing gader's single Dutch cardinal codes.

## Position in the pipeline

It runs **immediately after** the road-snapper (which supplies `_wegsegment`) and
before the RML mapper:

```
fetcher → <json> → roadSnapper → <snappedJson> → directionMapper → <enrichedJson> → mapper(RML) → translator → validator → poster
```

## How it works

For each record:

1. Read the German direction phrase from `thing.properties.richtung`, e.g.
   `"Nord nach Süd"`.
2. Parse it to a travel azimuth: `"X nach Y"` → the compass bearing of Y
   (`"Nord nach Süd"` = heading south = 180°). `"Keine Richtung"` → no bearing.
3. If there is **no bearing** (`"Keine Richtung"`), classify as `bothDirections`
   directly — no segment needed.
4. Otherwise read `_wegsegment.beginWkt` / `_wegsegment.eindWkt` and call gader's
   `classify_direction`: compare the travel bearing to the segment's start→end
   bearing.
   - within `threshold` (default 90°) → `inDirection`
   - otherwise → `inOppositeDirection`
5. Inject the result:

```jsonc
"_rijrichting": {
  "locationDirection":    "inDirection",   // the direction(s) the meetpunt measures
  "measurementDirection": "inDirection"    // the direction of this specific count
}
```

For Hamburg there is one direction per datastream, so `locationDirection` and
`measurementDirection` are the same value; the two fields are kept distinct so the
mapping can populate both the location-level `Rijrichting` and the
measurement-level kenmerk direction.

Records are **passed through unchanged** (no `_rijrichting` key) when the phrase
is missing or unparseable, or when the phrase is directional but no `_wegsegment`
was injected (the road-snapper skipped it) — a warning is logged, since there is
no reference bearing to classify against.

## Configuration (`processor.ttl`)

| Property | Required | Default | Description |
|---|---|---|---|
| `rdfc:reader` | yes | — | incoming (snapped) JSON stream |
| `rdfc:writer` | yes | — | outgoing (enriched) JSON stream |
| `rdfc:threshold` | no | `90.0` | max angle diff (°) for `inDirection` |

## Dependencies

- **shapely** — WKT parsing of the segment endpoints (for `classify_direction`)

## Testing

`tests/test_classify.py` verifies German phrase parsing, the
`inDirection`/`inOppositeDirection` classification against a real segment, and
that `DirectionLabel` values line up with the `cl-trt` codelist members. No
external data needed:

```bash
python3 tests/test_classify.py
```

## Building for the pipeline

```bash
cd processor/direction-mapper-py
hatch build -t sdist   # -> dist/rdfc_direction_mapper-0.0.1.tar.gz
```

The `dist/` output is build-only and git-ignored. Reference the sdist from the
pipeline's `pyproject.toml` under `[tool.uv.sources]`, then `hatch env create` in
`pipeline/` to install it.
