"""Standalone verification of the direction classification logic."""

import importlib.util
import os
import sys

_path = os.path.join(
    os.path.dirname(__file__), "..", "src", "rdfc_direction_mapper", "classify.py"
)
_spec = importlib.util.spec_from_file_location("_classify", _path)
_classify = importlib.util.module_from_spec(_spec)
sys.modules["_classify"] = _classify
_spec.loader.exec_module(_classify)

DirectionLabel = _classify.DirectionLabel
parse_german_direction = _classify.parse_german_direction
classify_direction = _classify.classify_direction
DirectionParseError = _classify.DirectionParseError


def main() -> int:
    # --- phrase parsing (the Hamburg-specific piece) ---
    assert parse_german_direction("Nord nach Süd") == 180.0
    assert parse_german_direction("Nordost nach Südwest") == 225.0
    assert parse_german_direction("Süd nach Nord") == 0.0
    assert parse_german_direction("Keine Richtung") is None
    assert parse_german_direction("Süd") == 180.0
    try:
        parse_german_direction("völlig unklar")
        raise AssertionError("expected DirectionParseError")
    except DirectionParseError:
        pass
    print("PASS: German phrase parsing")

    # --- classify against a real segment (gader logic, verbatim) ---
    # A segment running roughly N->S: start north, end south.
    start = "POINT (9.9178 53.4485)"   # north end
    end = "POINT (9.9178 53.4475)"     # south end
    # segment bearing start->end ≈ 180° (due south)

    # Measurement travelling south (180°) -> same as segment -> inDirection
    south = parse_german_direction("Nord nach Süd")  # 180
    assert classify_direction(south, start, end) == DirectionLabel.IN_DIRECTION

    # Measurement travelling north (0°) -> opposite of segment -> inOppositeDirection
    north = parse_german_direction("Süd nach Nord")  # 0
    assert (
        classify_direction(north, start, end) == DirectionLabel.IN_OPPOSITE_DIRECTION
    )
    print("PASS: classify inDirection / inOppositeDirection")

    # --- INSPIRE codelist values line up with the enum ---
    assert DirectionLabel.IN_DIRECTION.value == "inDirection"
    assert DirectionLabel.IN_OPPOSITE_DIRECTION.value == "inOppositeDirection"
    assert DirectionLabel.BOTH_DIRECTIONS.value == "bothDirections"
    print("PASS: DirectionLabel values match cl-trt LinkDirectionValue members")

    print("\nALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
