"""Add the de jure landed title tiers to every province.

For each province this adds, for the empire, kingdom, duchy and county it belongs to in
common/landed_titles (the de jure hierarchy, not who rules it):
  jure_empire / jure_kingdom / jure_duchy   the title key (null if the province sits under none)
  *_name, *_color                           localised name and official #RRGGBB color
  title_name, title_color                   the same for the county (title is already the county key)
  baronies                                  the baronies in the province with their holding types
  jure_chain                                "County < Duchy < Kingdom < Empire"

Usage (from the tools folder), after inject_government.py:
  python inject_titles.py --geojson provinces_gov.geojson --common path/to/common \\
      --history path/to/history --localisation path/to/localisation --out provinces_titles.geojson
"""
import argparse
import json
import sys
from pathlib import Path

import inject_government as ig
import inject_nations as inn
import parse_landed_titles as plt

TIER_PROPS = {"e": "jure_empire", "k": "jure_kingdom", "d": "jure_duchy"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="provinces_gov.geojson")
    parser.add_argument("--common", default="common")
    parser.add_argument("--history", default="history")
    parser.add_argument("--localisation", default="localisation")
    parser.add_argument("--out", default="provinces_titles.geojson")
    args = parser.parse_args()

    common, history = Path(args.common), Path(args.history)
    for folder in (common / "landed_titles", history / "provinces", Path(args.localisation)):
        if not folder.is_dir():
            sys.exit(f"{folder} is not a directory")

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)

    tree = ig.load_title_tree(common / "landed_titles")
    parent = {child: title for title, entry in tree.items() for child in entry["children"]}
    holdings = ig.load_province_holdings(history / "provinces")
    names = inn.load_names(Path(args.localisation))
    colors = plt.parse_landed_titles(common / "landed_titles")

    def info(title):
        name = inn.display_name(title, names)[0]
        return name, colors.get(title)

    counts = {"e": set(), "k": set(), "d": set()}
    guessed = set()
    for feature in collection["features"]:
        properties = feature["properties"]
        for prop in TIER_PROPS.values():
            properties[prop] = properties[prop + "_name"] = properties[prop + "_color"] = None
        properties["title_name"] = properties["title_color"] = properties["baronies"] = properties["jure_chain"] = None
        county = properties.get("title")
        if not county:
            continue
        properties["title_name"], properties["title_color"] = info(county)
        chain = [properties["title_name"]]
        title = county
        while title in parent:
            title = parent[title]
            prop = TIER_PROPS.get(title[0])
            if prop:
                name, color = info(title)
                properties[prop], properties[prop + "_name"], properties[prop + "_color"] = title, name, color
                chain.append(name)
                counts[title[0]].add(title)
                if not inn.display_name(title, names)[1]:
                    guessed.add(title)
        properties["jure_chain"] = " < ".join(chain)
        baronies = (holdings.get(properties["id"]) or (None, []))[1]
        order = {b: i for i, b in enumerate(tree.get(county, {}).get("children", []))}
        baronies = sorted(baronies, key=lambda item: order.get(item[0], 999))
        properties["baronies"] = "; ".join(f"{inn.display_name(b, names)[0]} ({kind})" for b, kind in baronies) or None

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))
    land = [f["properties"] for f in collection["features"] if f["properties"].get("title")]
    print(f"{len(land)} land provinces; de jure titles in use: {len(counts['e'])} empires, {len(counts['k'])} kingdoms, {len(counts['d'])} duchies, {len({p['title'] for p in land})} counties")
    for tier, prop in TIER_PROPS.items():
        print(f"  {sum(1 for p in land if p[prop] is None)} provinces have no de jure {prop[5:]}")
    if guessed:
        print(f"{len(guessed)} titles have no localisation and use a name built from the key", file=sys.stderr)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
