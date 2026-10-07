import argparse
import csv
import json
import sys
from pathlib import Path

import inject_history as ih

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


def display_name(title, names):
    if title in names:
        return names[title], True
    return title[2:].replace("_", " ").title(), False


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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="provinces_enriched.geojson")
    parser.add_argument("--history", default="history")
    parser.add_argument("--localisation", default="localisation")
    parser.add_argument("--out", default="provinces_nations.geojson")
    parser.add_argument("--date", default="2660.1.1")
    args = parser.parse_args()

    cutoff = ih.parse_date(args.date)
    if cutoff is None:
        sys.exit("--date must look like 2660.1.1")

    history = Path(args.history)
    titles_dir = history / "titles"
    provinces_dir = history / "provinces"
    for folder in (titles_dir, provinces_dir, Path(args.localisation)):
        if not folder.is_dir():
            sys.exit(f"{folder} is not a directory")

    titles = load_titles(titles_dir, cutoff)
    names = load_names(Path(args.localisation))
    province_titles, _ = ih.load_history(provinces_dir, {"title"}, None)

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)

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
        realms[top] = realms.get(top, 0) + 1
        assigned += 1

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))

    by_rank = {}
    for top in realms:
        by_rank[RANKS[top[0]]] = by_rank.get(RANKS[top[0]], 0) + 1
    print(f"date {args.date}: {assigned} provinces belong to {len(realms)} realms, wrote {args.out}")
    print("realms by top title rank: " + ", ".join(f"{count} {rank}" for rank, count in sorted(by_rank.items(), key=lambda item: -item[1])))
    if unowned:
        print(f"{unowned} counties have no holder on {args.date} and were left without a nation", file=sys.stderr)
    if guessed:
        print(f"{len(guessed)} realm titles have no localisation and use a name built from the title key (first few: {sorted(guessed)[:8]})", file=sys.stderr)
    largest = sorted(realms.items(), key=lambda item: -item[1])[:8]
    print("largest: " + ", ".join(f"{display_name(t, names)[0]} ({n})" for t, n in largest), file=sys.stderr)


if __name__ == "__main__":
    main()
