#!/usr/bin/env python3
"""Enrich a province GeoJSON with CK2 (After the End) data and build a trade-route overlay.

Usage
-----
    python build_map_data.py \
        --geojson map_data.geojson \
        --common  path/to/common \
        --out-dir out \
        [--localisation path/to/localisation]

Reads
    <common>/landed_titles/landed_titles.txt
    <common>/religions/*.txt
    <common>/cultures/*.txt
    <common>/trade_routes/*.txt
    --geojson   province FeatureCollection (an inject_nations.py-style file with
                `culture`, `religion`, `title`, `nation_title` properties, or a bare
                id + geometry file; a `window.X = {...};` .js wrapper is also accepted)

Writes (into --out-dir)
    provinces.geojson      input + realm_color, religion_color, culture_color,
                           title_name, and a fallback-cleaned `nation` name
    trade_routes.geojson   FeatureCollection of LineStrings, one per `path = { ... }` block

Coordinates are written exactly as they appear in the input geometry ([x, y], y-up
pixel space), so both files line up under Leaflet's L.CRS.Simple with L.geoJSON().
Standard library only.
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

TIER_PREFIX = re.compile(r"^[ekdcb]_")
TITLE_KEY = re.compile(r"^[ekdcb]_\S+$")
TOKEN = re.compile(r'#[^\n]*|"[^"]*"|[{}=]|[^\s{}="#]+')
NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")


# --------------------------------------------------------------------------- #
# Reading + generic CK2 script parser
# --------------------------------------------------------------------------- #
def read_text(path):
    """CK2 files are cp1252/latin-1; never crash on a stray byte."""
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:  # cp1252 leaves 5 bytes undefined
        return raw.decode("latin-1")


def tokenize(text):
    return [t for t in TOKEN.findall(text) if not t.startswith("#")]


def _unquote(value):
    return value[1:-1] if len(value) >= 2 and value[0] == value[-1] == '"' else value


def parse_block(tokens, i=0):
    """Parse `key = value` pairs until the matching `}`.

    Returns (pairs, next_index). A value is a string or a nested list of pairs.
    Bare tokens (e.g. the numbers inside `color = { 1 2 3 }`) become (None, token).
    """
    pairs = []
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if tok == "}":
            return pairs, i + 1
        if tok in ("{", "="):
            i += 1
            continue
        if i + 2 < n and tokens[i + 1] == "=":
            value = tokens[i + 2]
            if value == "{":
                sub, i = parse_block(tokens, i + 3)
                pairs.append((_unquote(tok), sub))
            else:
                pairs.append((_unquote(tok), _unquote(value)))
                i += 3
        else:
            pairs.append((None, _unquote(tok)))
            i += 1
    return pairs, i


def parse_file(path):
    pairs, _ = parse_block(tokenize(read_text(path)))
    return pairs


def bare_values(block):
    return [v for k, v in block if k is None and isinstance(v, str)]


def txt_files(folder):
    folder = Path(folder)
    if folder.is_file():
        return [folder]
    return sorted(p for p in folder.glob("*.txt") if p.is_file())


# --------------------------------------------------------------------------- #
# 1. Title name fallback cleaner
# --------------------------------------------------------------------------- #
def clean_title_name(key):
    """c_ciudad_valles -> 'Ciudad Valles'.

    Strips the tier prefix, swaps underscores for spaces and title-cases each word.
    Words are capitalised by hand rather than with str.title(), which would turn
    "st_john's" into "St John'S".
    """
    return _title_case(TIER_PREFIX.sub("", key.strip(), count=1))


def _title_case(text):
    return " ".join(w[:1].upper() + w[1:].lower() for w in text.replace("_", " ").split())


# --------------------------------------------------------------------------- #
# 2. Color normalizer
# --------------------------------------------------------------------------- #
def normalize_color(values):
    """Turn the numbers of a CK2 `color = { R G B }` block into '#RRGGBB'.

    * any value containing a decimal point -> 0.0-1.0 floats, each * 255 and rounded
    * otherwise integers 0-255, used as-is
    Returns None if the block does not hold three numbers.
    """
    nums = [str(v) for v in values if NUMBER.match(str(v))][:3]
    if len(nums) < 3:
        return None
    floats = [float(v) for v in nums]
    if any("." in v for v in nums) and max(floats) <= 1.0:
        floats = [v * 255 for v in floats]
    r, g, b = (max(0, min(255, int(round(v)))) for v in floats)
    return "#{:02x}{:02x}{:02x}".format(r, g, b)


def block_color(block):
    """Colour of a block's own direct `color = { ... }` child (ignores color2 etc.)."""
    for k, v in block:
        if k == "color" and isinstance(v, list):
            return normalize_color(bare_values(v))
    return None


# --------------------------------------------------------------------------- #
# 3. Parsers
# --------------------------------------------------------------------------- #
def parse_landed_titles(path):
    """{title_key: '#rrggbb'} for every e_/k_/d_/c_/b_ block that has its own color.

    The file is nested (empire > kingdom > duchy > county > barony), so a color is
    only taken from the block it is directly inside, never from a child's.
    """
    colors = {}

    def walk(pairs):
        for key, value in pairs:
            if not isinstance(value, list):
                continue
            if key and TITLE_KEY.match(key):
                color = block_color(value)
                if color and key not in colors:
                    colors[key] = color
            walk(value)

    for file in txt_files(path):
        walk(parse_file(file))
    return colors


def parse_named_colors(folder):
    """{name: '#rrggbb'} for religions or cultures.

    Both are nested one or two levels deep (group > religion/culture), so every
    named block that directly contains a `color = { ... }` is registered. Names in
    later files (alphabetical) override earlier ones, as mod files do in-game.
    """
    found = {}

    def walk(pairs):
        for key, value in pairs:
            if key and isinstance(value, list):
                color = block_color(value)
                if color:
                    found[key] = color
                walk(value)

    for file in txt_files(folder):
        walk(parse_file(file))
    return found


def parse_trade_routes(folder):
    """[(route_name, route_color_or_None, [path, path, ...])] where a path is a list of int ids.

    Every `path = { id id id }` anywhere inside a route block is collected, so routes
    with several paths (branches) are kept whole.
    """
    routes = []

    def collect(pairs, out):
        for key, value in pairs:
            if isinstance(value, list):
                if key == "path":
                    ids = [int(v) for v in bare_values(value) if v.isdigit()]
                    if ids:
                        out.append(ids)
                else:
                    collect(value, out)

    for file in txt_files(folder):
        for name, body in parse_file(file):
            if name and isinstance(body, list):
                paths = []
                collect(body, paths)
                if paths:
                    routes.append((name, block_color(body), paths))
    return routes


def load_localisation(folder):
    """{title_key: display name} from semicolon-separated localisation .csv files."""
    names = {}
    for path in sorted(Path(folder).glob("*.csv")):
        text = read_text(path)
        for row in csv.reader(text.splitlines(), delimiter=";"):
            if len(row) >= 2:
                key, value = row[0].strip(), row[1].strip()
                if TITLE_KEY.match(key) and value and key not in names:
                    names[key] = value
    return names


def title_display_name(key, names):
    """Localised name, or the cleaned key when it is missing / just repeats the key."""
    name = names.get(key)
    if name and name.lower() != key.lower():
        return name
    return clean_title_name(key)


# --------------------------------------------------------------------------- #
# Geometry
# --------------------------------------------------------------------------- #
def _ring_centroid(ring):
    """(signed_area, cx, cy) of a closed ring via the shoelace formula."""
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(ring, ring[1:]):
        cross = x0 * y1 - x1 * y0
        a += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    a /= 2.0
    if abs(a) < 1e-12:
        return 0.0, None, None
    return a, cx / (6.0 * a), cy / (6.0 * a)


def _polygon_centroid(rings):
    """Area-weighted centroid of a polygon (outer ring minus holes) -> (area, x, y)."""
    area = sx = sy = 0.0
    for idx, ring in enumerate(rings):
        a, cx, cy = _ring_centroid(ring)
        if cx is None:
            continue
        sign = 1.0 if idx == 0 else -1.0
        a = abs(a) * sign
        area += a
        sx += cx * a
        sy += cy * a
    if abs(area) < 1e-12:
        return 0.0, None, None
    return area, sx / area, sy / area


def geometry_centroid(geometry):
    """Centroid of the largest polygon part of a Polygon/MultiPolygon.

    The biggest part is used (not the whole multipolygon) so a tiny island far away
    cannot drag a route node out to sea. Falls back to the bounding-box centre.
    """
    if not geometry:
        return None
    kind, coords = geometry.get("type"), geometry.get("coordinates")
    if kind == "Polygon":
        polygons = [coords]
    elif kind == "MultiPolygon":
        polygons = coords
    else:
        return None
    best = None
    for rings in polygons:
        area, x, y = _polygon_centroid(rings)
        if x is not None and (best is None or area > best[0]):
            best = (area, x, y)
    if best:
        return [round(best[1], 3), round(best[2], 3)]
    pts = [p for rings in polygons for ring in rings for p in ring]
    if not pts:
        return None
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [round((min(xs) + max(xs)) / 2, 3), round((min(ys) + max(ys)) / 2, 3)]


# --------------------------------------------------------------------------- #
# GeoJSON I/O
# --------------------------------------------------------------------------- #
def load_geojson(path):
    text = read_text(path)
    start = text.index("{")  # tolerates `window.PROVINCES_DATA = {...};`
    return json.loads(text[start:].rstrip().rstrip(";"))


def province_id(feature):
    props = feature.get("properties") or {}
    for key in ("id", "ID", "province_id", "province", "Province"):
        if props.get(key) is not None:
            try:
                return int(props[key])
            except (TypeError, ValueError):
                pass
    try:
        return int(feature.get("id"))
    except (TypeError, ValueError):
        return None


def write_json(path, data):
    Path(path).write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Main steps
# --------------------------------------------------------------------------- #
def enrich_provinces(collection, realm_colors, religion_colors, culture_colors, names):
    stats = dict(realm=0, religion=0, culture=0, fallback_names=0, missing_religion=set(), missing_culture=set())
    for feature in collection["features"]:
        props = feature.setdefault("properties", {})

        # realm_color: the realm's top title, else the county's own title
        realm_color = None
        for key in (props.get("nation_title"), props.get("title")):
            if key and key in realm_colors:
                realm_color = realm_colors[key]
                break
        props["realm_color"] = realm_color or props.get("realm_color")  # keep an earlier color if none found
        stats["realm"] += realm_color is not None

        religion, culture = props.get("religion"), props.get("culture")
        props["religion_color"] = religion_colors.get(religion) if religion else None
        props["culture_color"] = culture_colors.get(culture) if culture else None
        stats["religion"] += props["religion_color"] is not None
        stats["culture"] += props["culture_color"] is not None
        if religion and religion not in religion_colors:
            stats["missing_religion"].add(religion)
        if culture and culture not in culture_colors:
            stats["missing_culture"].add(culture)

        # names: county name for `title`, realm name for `nation_title`
        if props.get("title"):
            props["title_name"] = title_display_name(props["title"], names)
            stats["fallback_names"] += props["title_name"] == clean_title_name(props["title"]) and props["title"] not in names
        if props.get("nation_title"):
            current = props.get("nation")
            if not current or current.lower() == props["nation_title"].lower():
                props["nation"] = title_display_name(props["nation_title"], names)
                stats["fallback_names"] += 1
    return stats


def build_trade_routes(collection, routes):
    centroids, missing = {}, set()
    for feature in collection["features"]:
        pid = province_id(feature)
        if pid is not None:
            c = geometry_centroid(feature.get("geometry"))
            if c:
                centroids[pid] = c

    features = []
    for name, color, paths in routes:
        for index, ids in enumerate(paths):
            kept = [pid for pid in ids if pid in centroids]
            missing.update(pid for pid in ids if pid not in centroids)
            if len(kept) < 2:  # a LineString needs two points
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "name": name,
                    "display_name": _title_case(name),
                    "path_index": index,
                    "color": color,
                    "province_ids": kept,
                },
                "geometry": {"type": "LineString", "coordinates": [centroids[pid] for pid in kept]},
            })
    return {"type": "FeatureCollection", "features": features}, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--geojson", required=True, help="province map_data.geojson (or provinces_nations.geojson)")
    ap.add_argument("--common", required=True, help="path to the mod's common/ folder")
    ap.add_argument("--localisation", help="optional localisation folder, for real title names")
    ap.add_argument("--out-dir", default=".", help="where provinces.geojson and trade_routes.geojson go")
    args = ap.parse_args()

    common = Path(args.common)
    paths = {
        # the whole folder: titles live in landed_titles.txt AND the regional files beside it
        "landed": common / "landed_titles",
        "religions": common / "religions",
        "cultures": common / "cultures",
        "routes": common / "trade_routes",
    }
    for label, p in paths.items():
        if not p.exists():
            sys.exit(f"missing {label}: {p}")

    collection = load_geojson(args.geojson)
    realm_colors = parse_landed_titles(paths["landed"])
    religion_colors = parse_named_colors(paths["religions"])
    culture_colors = parse_named_colors(paths["cultures"])
    routes = parse_trade_routes(paths["routes"])
    names = load_localisation(args.localisation) if args.localisation else {}

    print(f"landed titles with color: {len(realm_colors)}")
    print(f"religions with color:     {len(religion_colors)}")
    print(f"cultures with color:      {len(culture_colors)}")
    print(f"trade routes:             {len(routes)} ({sum(len(r[2]) for r in routes)} paths)")

    stats = enrich_provinces(collection, realm_colors, religion_colors, culture_colors, names)
    trade, missing = build_trade_routes(collection, routes)

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    write_json(out / "provinces.geojson", collection)
    write_json(out / "trade_routes.geojson", trade)

    total = len(collection["features"])
    print(f"\nprovinces: {total}")
    print(f"  realm_color    {stats['realm']}")
    print(f"  religion_color {stats['religion']}")
    print(f"  culture_color  {stats['culture']}")
    print(f"  names built from title keys: {stats['fallback_names']}")
    if stats["missing_religion"]:
        print(f"  religions with no color: {sorted(stats['missing_religion'])[:10]}", file=sys.stderr)
    if stats["missing_culture"]:
        print(f"  cultures with no color:  {sorted(stats['missing_culture'])[:10]}", file=sys.stderr)
    print(f"trade route lines: {len(trade['features'])}")
    if missing:
        print(f"  {len(missing)} path province ids have no geometry (skipped): {sorted(missing)[:15]}", file=sys.stderr)
    print(f"wrote {out / 'provinces.geojson'} and {out / 'trade_routes.geojson'}")


if __name__ == "__main__":
    main()
