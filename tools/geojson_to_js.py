import argparse
import json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="provinces_enriched.geojson")
    parser.add_argument("--out", default="../data/provinces.js")
    parser.add_argument("--var", default="PROVINCES_DATA", help="window variable name, e.g. TRADE_ROUTES_DATA")
    args = parser.parse_args()

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)

    with open(args.out, "w", encoding="utf-8") as handle:
        handle.write(f"window.{args.var}=")
        json.dump(collection, handle, separators=(",", ":"))
        handle.write(";")

    print(f"wrote {len(collection['features'])} features to {args.out}")


if __name__ == "__main__":
    main()
