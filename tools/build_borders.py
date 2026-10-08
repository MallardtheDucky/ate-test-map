"""Build the border lines the map draws over Nation and Vassal modes.

Two provinces share a border wherever the same edge appears in both polygons. An edge
between two provinces of different nations is a nation border; an edge between two
provinces of the same nation but different vassal realms is a vassal border. Edges are
joined into continuous lines and written as data/borders.js (window.BORDERS_DATA).

Usage (from the tools folder), after build_map_data.py:
  python build_borders.py --geojson out/provinces.geojson --out ../data/borders.js
"""
import argparse
import json
import sys
from collections import defaultdict


def rings(geometry):
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "MultiPolygon":
        return [ring for polygon in geometry["coordinates"] for ring in polygon]
    return []


def chain(segments):
    """Join (a, b) point pairs into polylines wherever segments meet end to end."""
    adjacent = defaultdict(list)
    for index, (a, b) in enumerate(segments):
        adjacent[a].append(index)
        adjacent[b].append(index)
    used = set()

    def extend(line, at_end):
        while True:
            tip = line[-1] if at_end else line[0]
            step = next((i for i in adjacent[tip] if i not in used), None)
            if step is None:
                return
            used.add(step)
            a, b = segments[step]
            other = b if a == tip else a
            if at_end:
                line.append(other)
            else:
                line.insert(0, other)

    lines = []
    for index, (a, b) in enumerate(segments):
        if index in used:
            continue
        used.add(index)
        line = [a, b]
        extend(line, True)
        extend(line, False)
        lines.append(line)
    return lines


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="out/provinces.geojson", help="provinces.geojson from build_map_data.py")
    parser.add_argument("--out", default="../data/borders.js")
    args = parser.parse_args()

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)

    owners = defaultdict(set)  # rounded edge -> provinces using it
    points = {}
    for feature in collection["features"]:
        province = feature["properties"]
        if not province.get("nation_title"):
            continue
        for ring in rings(feature["geometry"]):
            for a, b in zip(ring, ring[1:]):
                pa, pb = (round(a[0], 2), round(a[1], 2)), (round(b[0], 2), round(b[1], 2))
                if pa == pb:
                    continue
                edge = (pa, pb) if pa < pb else (pb, pa)
                owners[edge].add(province["id"])
                points[pa], points[pb] = a, b
    info = {f["properties"]["id"]: f["properties"] for f in collection["features"]}

    nation, vassal = [], []
    for edge, ids in owners.items():
        if len(ids) != 2:
            continue
        p, q = (info[i] for i in ids)
        if p["nation_title"] != q["nation_title"]:
            nation.append(edge)
        elif p.get("vassal_title") != q.get("vassal_title"):
            vassal.append(edge)

    def lines_of(edges):
        return [[[int(round(points[pt][0])), int(round(points[pt][1]))] for pt in line] for line in chain(edges)]

    result = {"nation": lines_of(nation), "vassal": lines_of(vassal)}
    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("window.BORDERS_DATA=")
        json.dump(result, handle, separators=(",", ":"))
        handle.write(";\n")
    print(f"{len(nation)} nation border edges in {len(result['nation'])} lines, {len(vassal)} vassal border edges in {len(result['vassal'])} lines -> {args.out}")


if __name__ == "__main__":
    main()
