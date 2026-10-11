"""Build border-ribbon rings around each raion for the curtain renderer.

For every raion polygon in web/data/raions.geojson, buffer its boundary by
~0.015 deg to get a ring straddling the border (MultiPolygon exteriors handled
by shapely). Output: web/data/ribbons.geojson, properties.key preserved.
"""
import json
import pathlib

from shapely.geometry import mapping, shape

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "web" / "data" / "raions.geojson"
OUT = ROOT / "web" / "data" / "ribbons.geojson"


def round_coords(c):
    if isinstance(c[0], float):
        return [round(c[0], 4), round(c[1], 4)]
    return [round_coords(x) for x in c]


def main():
    src = json.loads(SRC.read_text(encoding="utf-8"))
    feats = []
    for f in src["features"]:
        geom = shape(f["geometry"]).buffer(0)  # fix self-intersections
        ribbon = geom.boundary.buffer(0.015, quad_segs=4).simplify(0.003)
        m = mapping(ribbon)
        feats.append({
            "type": "Feature",
            "properties": {"key": f["properties"]["key"]},
            "geometry": {"type": m["type"], "coordinates": round_coords(m["coordinates"])},
        })
    OUT.write_text(
        json.dumps({"type": "FeatureCollection", "features": feats},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"{len(feats)} ribbons -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
