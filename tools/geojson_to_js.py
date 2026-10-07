import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="provinces_enriched.geojson")
    parser.add_argument("--out", default="../data/provinces.js")
    args = parser.parse_args()

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write("window.PROVINCES_DATA=")
        json.dump(collection, handle, separators=(",", ":"))
        handle.write(";")

    print(f"wrote {len(collection['features'])} features to {args.out}")


if __name__ == "__main__":
    main()
