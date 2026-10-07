import argparse
import csv
import json
import re
import sys
from pathlib import Path

import inject_history as ih
import parse_landed_titles as plt

RANKS = {"b": "barony", "c": "county", "d": "duchy", "k": "kingdom", "e": "empire"}


def latest(pairs, key, cutoff):
    value = None
    for k, v in pairs:
        if k == key and isinstance(v, str):
            value = v
    dated = []
    for k, v in pairs:
        if k is None or not isinstance(v, list):
            continue
        stamp = ih.parse_date(k)
        if stamp is not None:
            dated.append((stamp, v))
    dated.sort(key=lambda item: item[0])
    for stamp, block in dated:
        if stamp > cutoff:
            break
        for k, v in block:
            if k == key and isinstance(v, str):
                value = v
    return value


def load_titles(folder, cutoff):
    titles = {}
    for path in sorted(folder.glob("*.txt")):
        pairs, _ = ih.parse_block(ih.tokenize(ih.read_text(path)), 0)
        liege = latest(pairs, "liege", cutoff)
        titles[path.stem] = {
            "holder": latest(pairs, "holder", cutoff),
            "liege": None if liege in (None, "0", "") else liege,
            "active": latest(pairs, "active", cutoff),
        }
    return titles


def load_names(folder):
    names = {}
    for path in sorted(folder.glob("*.csv")):
        with open(path, encoding="cp1252", errors="replace", newline="") as handle:
            for row in csv.reader(handle, delimiter=";"):
                if len(row) < 2:
                    continue
                key = row[0].strip()
                if len(key) > 2 and key[1] == "_" and key[0] in RANKS and key not in names and row[1].strip():
                    names[key] = row[1].strip()
    return names


PREFIX_PATTERN = re.compile(r"^[bcdke]_")
RAW_KEY_PATTERN = re.compile(r"^[bcdke]_[a-z0-9_'\-]+$")


def clean_title_key(key):
    """Fallback name for a title with no localisation.

    'c_ciudad_valles' -> 'Ciudad Valles', 'd_fargo' -> 'Fargo'.
    Strips the b_/c_/d_/k_/e_ prefix, turns underscores into spaces and
    capitalises each word (without str.title(), which turns "st_john's"
    into "St John'S").
    """
    stripped = PREFIX_PATTERN.sub("", key.strip(), count=1)
    words = stripped.replace("_", " ").split()
    return " ".join(word[:1].upper() + word[1:] for word in words)


def display_name(title, names):
    """Return (name, found). found is False when the name had to be built from the key."""
    name = names.get(title)
    # A localisation row that just repeats the raw key (e.g. "d_fargo;d_fargo") counts as missing.
    if name and not (RAW_KEY_PATTERN.match(name) and name.lower() == title.lower()):
        return name, True
    return clean_title_key(title), False


def is_held(titles, title):
    entry = titles.get(title)
    if entry is None:
        return False
    return entry["holder"] not in (None, "0") and entry["active"] != "no"


def realm_chain(titles, county):
    chain = [county]
    while True:
        liege = titles[chain[-1]]["liege"]
        if liege is None or not is_held(titles, liege) or liege in chain:
            return chain
        chain.append(liege)


def title_dates(folder):
    stamps = set()
    for path in sorted(folder.glob("*.txt")):
        pairs, _ = ih.parse_block(ih.tokenize(ih.read_text(path)), 0)
        for k, v in pairs:
            if k is not None and isinstance(v, list):
                stamp = ih.parse_date(k)
                if stamp is not None:
                    stamps.add(stamp)
    return sorted(stamps)


def first_full_date(folder, counties, floor=(0, 0, 0)):
    """Earliest date on which every county on the map has a holder."""
    best = None
    for stamp in title_dates(folder):
        if stamp < floor:
            continue
        titles = load_titles(folder, stamp)
        held = sum(1 for county in counties if county in titles and is_held(titles, county))
        if best is None or held > best[1]:
            best = (stamp, held)
        if held == len(counties):
            return stamp, held
    return best


def sea_zone_ids(default_map):
    """Province ids listed as sea_zones ranges before the ocean_region blocks."""
    text = ih.read_text(default_map)
    head = text.split("ocean_region")[0]
    ids = set()
    for low, high in re.findall(r"sea_zones\s*=\s*\{\s*(\d+)\s+(\d+)\s*\}", head):
        ids.update(range(int(low), int(high) + 1))
    return ids


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="provinces_enriched.geojson")
    parser.add_argument("--history", default="history")
    parser.add_argument("--localisation", default="localisation")
    parser.add_argument("--default-map", default=None, help="path to map/default.map, used to tell sea zones from wasteland")
    parser.add_argument("--out", default="provinces_nations.geojson")
    parser.add_argument("--landed-titles", default=None, help="path to common/landed_titles/landed_titles.txt (or the folder); adds a realm_color hex property")
    parser.add_argument("--date", default="auto", help="a date like 2666.1.1, or auto for the first date every county has a holder")
    args = parser.parse_args()

    history = Path(args.history)
    titles_dir = history / "titles"
    provinces_dir = history / "provinces"
    for folder in (titles_dir, provinces_dir, Path(args.localisation)):
        if not folder.is_dir():
            sys.exit(f"{folder} is not a directory")

    province_titles, _ = ih.load_history(provinces_dir, {"title"}, None)

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)

    if args.date == "auto":
        counties = {
            province_titles[f["properties"]["id"]]["title"]
            for f in collection["features"]
            if province_titles.get(f["properties"]["id"], {}).get("title")
        }
        found = first_full_date(titles_dir, counties)
        if found is None:
            sys.exit("no title dates found")
        cutoff, held = found
        args.date = ".".join(str(part) for part in cutoff)
        note = "every county" if held == len(counties) else f"{held} of {len(counties)} counties (the most any date reaches)"
        print(f"auto date: {args.date}, the first date with a holder for {note}")
    else:
        cutoff = ih.parse_date(args.date)
        if cutoff is None:
            sys.exit("--date must look like 2666.1.1 or auto")

    sea = set()
    if args.default_map:
        sea = sea_zone_ids(Path(args.default_map))
        if not sea:
            print("no sea_zones found in default.map, sea and wasteland will not be told apart", file=sys.stderr)

    titles = load_titles(titles_dir, cutoff)
    names = load_names(Path(args.localisation))

    realm_colors = {}
    if args.landed_titles:
        landed = Path(args.landed_titles)
        if not landed.exists():
            sys.exit(f"{landed} does not exist")
        realm_colors = plt.parse_landed_titles(landed)
        print(f"read {len(realm_colors)} title colors from {landed}")

    guessed = set()
    assigned = 0
    unowned = 0
    realms = {}
    for feature in collection["features"]:
        properties = feature["properties"]
        county = province_titles.get(properties["id"], {}).get("title")
        properties["title"] = county
        properties["nation_title"] = None
        properties["nation"] = None
        properties["nation_rank"] = None
        properties["realm_color"] = None
        properties["kind"] = "sea" if properties["id"] in sea else ("land" if county else "wasteland")
        if county is None or county not in titles:
            continue
        if not is_held(titles, county):
            unowned += 1
            continue
        top = realm_chain(titles, county)[-1]
        name, found = display_name(top, names)
        if not found:
            guessed.add(top)
        properties["nation_title"] = top
        properties["nation"] = name
        properties["nation_rank"] = RANKS[top[0]]
        properties["realm_color"] = realm_colors.get(top)
        realms[top] = realms.get(top, 0) + 1
        assigned += 1

    collection["nation_date"] = args.date
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))

    by_rank = {}
    for top in realms:
        by_rank[RANKS[top[0]]] = by_rank.get(RANKS[top[0]], 0) + 1
    print(f"date {args.date}: {assigned} provinces belong to {len(realms)} realms, wrote {args.out}")
    print("realms by top title rank: " + ", ".join(f"{count} {rank}" for rank, count in sorted(by_rank.items(), key=lambda item: -item[1])))
    if unowned:
        print(f"{unowned} counties have no holder on {args.date} and were left without a nation", file=sys.stderr)
    if realm_colors:
        no_color = sorted(top for top in realms if top not in realm_colors)
        if no_color:
            print(f"{len(no_color)} realm titles have no color block in landed_titles and get no realm_color (first few: {no_color[:8]})", file=sys.stderr)
    if guessed:
        print(f"{len(guessed)} realm titles have no localisation and use a name built from the title key (first few: {sorted(guessed)[:8]})", file=sys.stderr)
    largest = sorted(realms.items(), key=lambda item: -item[1])[:8]
    print("largest: " + ", ".join(f"{display_name(t, names)[0]} ({n})" for t, n in largest), file=sys.stderr)


if __name__ == "__main__":
    main()
