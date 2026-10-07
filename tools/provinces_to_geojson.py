import argparse
import csv
import json
import sys

import cv2
import numpy as np
from rasterio.features import shapes
from rasterio.transform import Affine
from shapely.geometry import MultiPolygon, mapping, shape


def load_definitions(path):
    definitions = {}
    with open(path, encoding="cp1252", newline="") as handle:
        for row in csv.reader(handle, delimiter=";"):
            if len(row) < 5:
                continue
            try:
                province_id = int(row[0])
                r, g, b = int(row[1]), int(row[2]), int(row[3])
            except ValueError:
                continue
            key = (r << 16) | (g << 8) | b
            if key in definitions:
                print(f"duplicate color {r},{g},{b} for province {province_id}, keeping province {definitions[key]['id']}", file=sys.stderr)
                continue
            definitions[key] = {"id": province_id, "name": row[4].strip(), "rgb": (r, g, b)}
    return definitions


def build_label_raster(rgb, definitions):
    packed = (
        (rgb[:, :, 0].astype(np.int32) << 16)
        | (rgb[:, :, 1].astype(np.int32) << 8)
        | rgb[:, :, 2].astype(np.int32)
    )
    unique_colors, inverse = np.unique(packed.ravel(), return_inverse=True)
    keys = list(definitions)
    key_to_label = {key: index + 1 for index, key in enumerate(keys)}
    lookup = np.array([key_to_label.get(int(c), 0) for c in unique_colors], dtype=np.int32)
    labels = lookup[inverse].reshape(packed.shape)
    unmapped = [int(c) for c, label in zip(unique_colors, lookup) if label == 0]
    seen_labels = set(int(v) for v in np.unique(labels) if v)
    missing_from_image = [definitions[keys[label - 1]]["id"] for label in range(1, len(keys) + 1) if label not in seen_labels]
    return labels, keys, unmapped, missing_from_image


def round_coordinates(value, digits):
    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (int, float)):
            return [round(v, digits) for v in value]
        return [round_coordinates(v, digits) for v in value]
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bmp", default="provinces.bmp")
    parser.add_argument("--csv", default="definition.csv")
    parser.add_argument("--out", default="provinces.geojson")
    parser.add_argument("--simplify", type=float, default=0.75)
    parser.add_argument("--precision", type=int, default=2)
    parser.add_argument("--image-coords", action="store_true")
    args = parser.parse_args()

    image = cv2.imread(args.bmp, cv2.IMREAD_COLOR)
    if image is None:
        sys.exit(f"could not read {args.bmp}")
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width = rgb.shape[:2]

    definitions = load_definitions(args.csv)
    if not definitions:
        sys.exit(f"no usable rows in {args.csv}")

    labels, keys, unmapped, missing_from_image = build_label_raster(rgb, definitions)

    if unmapped:
        sample = ", ".join("#%06x" % c for c in unmapped[:10])
        print(f"{len(unmapped)} image colors have no CSV entry and were skipped (first few: {sample})", file=sys.stderr)
    if missing_from_image:
        print(f"{len(missing_from_image)} CSV provinces never appear in the image (first few ids: {missing_from_image[:10]})", file=sys.stderr)

    if args.image_coords:
        transform = Affine(1, 0, 0, 0, 1, 0)
    else:
        transform = Affine(1, 0, 0, 0, -1, height)

    polygons_by_label = {}
    for geometry, value in shapes(labels, mask=labels > 0, transform=transform, connectivity=4):
        label = int(value)
        polygon = shape(geometry)
        if args.simplify > 0:
            polygon = polygon.simplify(args.simplify, preserve_topology=True)
        if polygon.is_empty:
            continue
        polygons_by_label.setdefault(label, []).append(polygon)

    features = []
    for label in sorted(polygons_by_label):
        info = definitions[keys[label - 1]]
        parts = polygons_by_label[label]
        geometry = mapping(parts[0] if len(parts) == 1 else MultiPolygon(parts))
        geometry = {
            "type": geometry["type"],
            "coordinates": round_coordinates(geometry["coordinates"], args.precision),
        }
        r, g, b = info["rgb"]
        features.append(
            {
                "type": "Feature",
                "id": info["id"],
                "properties": {
                    "id": info["id"],
                    "name": info["name"],
                    "color": f"#{r:02x}{g:02x}{b:02x}",
                },
                "geometry": geometry,
            }
        )

    collection = {"type": "FeatureCollection", "features": features}
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))

    print(f"wrote {len(features)} provinces from {width}x{height} image to {args.out}")


if __name__ == "__main__":
    main()
