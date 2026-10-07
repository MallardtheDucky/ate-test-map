import argparse
import json
import re
import sys
from pathlib import Path

TOKEN_PATTERN = re.compile(r'#[^\n]*|"[^"]*"|[{}=]|[^\s{}="#]+')
DATE_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
ID_PATTERN = re.compile(r"^\s*(\d+)")


def read_text(path):
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("cp1252", errors="replace")


def tokenize(text):
    return [t for t in TOKEN_PATTERN.findall(text) if not t.startswith("#")]


def unquote(value):
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        return value[1:-1]
    return value


def parse_block(tokens, index):
    pairs = []
    while index < len(tokens):
        token = tokens[index]
        if token == "}":
            return pairs, index + 1
        if token in ("{", "="):
            index += 1
            continue
        if index + 1 < len(tokens) and tokens[index + 1] == "=":
            if index + 2 >= len(tokens):
                break
            value = tokens[index + 2]
            if value == "{":
                sub_pairs, index = parse_block(tokens, index + 3)
                pairs.append((unquote(token), sub_pairs))
            else:
                pairs.append((unquote(token), unquote(value)))
                index += 3
        else:
            pairs.append((None, unquote(token)))
            index += 1
    return pairs, index


def parse_date(key):
    match = DATE_PATTERN.match(key)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def resolve_fields(pairs, fields, cutoff):
    state = {}
    for key, value in pairs:
        if key in fields and isinstance(value, str):
            state[key] = value

    dated = []
    for key, value in pairs:
        if key is None or not isinstance(value, list):
            continue
        stamp = parse_date(key)
        if stamp is not None:
            dated.append((stamp, value))
    dated.sort(key=lambda item: item[0])

    if cutoff is not None:
        for stamp, block in dated:
            if stamp > cutoff:
                break
            for key, value in block:
                if key in fields and isinstance(value, str):
                    state[key] = value
    return state


def load_history(folder, fields, cutoff):
    history = {}
    skipped = []
    for path in sorted(folder.glob("*.txt")):
        match = ID_PATTERN.match(path.stem)
        if not match:
            skipped.append(path.name)
            continue
        province_id = int(match.group(1))
        tokens = tokenize(read_text(path))
        pairs, _ = parse_block(tokens, 0)
        state = resolve_fields(pairs, fields, cutoff)
        if province_id in history:
            print(f"province {province_id} has more than one history file, '{path.name}' overrides the earlier one", file=sys.stderr)
        history[province_id] = state
    return history, skipped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="provinces.geojson")
    parser.add_argument("--history", default="history/provinces")
    parser.add_argument("--out", default="provinces_enriched.geojson")
    parser.add_argument("--fields", default="culture,religion")
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    fields = [name.strip() for name in args.fields.split(",") if name.strip()]
    if not fields:
        sys.exit("no fields requested")

    cutoff = None
    if args.date:
        cutoff = parse_date(args.date)
        if cutoff is None:
            sys.exit("--date must look like 1066.9.15")

    folder = Path(args.history)
    if not folder.is_dir():
        sys.exit(f"{folder} is not a directory")

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)

    history, skipped = load_history(folder, set(fields), cutoff)

    matched = 0
    without_history = []
    feature_ids = set()
    for feature in collection["features"]:
        properties = feature["properties"]
        province_id = properties["id"]
        feature_ids.add(province_id)
        state = history.get(province_id)
        if state is None:
            without_history.append(province_id)
            for name in fields:
                properties[name] = None
            continue
        matched += 1
        for name in fields:
            properties[name] = state.get(name)

    orphan_files = sorted(set(history) - feature_ids)

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))

    print(f"parsed {len(history)} history files, enriched {matched} of {len(collection['features'])} features, wrote {args.out}")
    if skipped:
        print(f"{len(skipped)} files had no leading province id and were skipped (first few: {skipped[:5]})", file=sys.stderr)
    if without_history:
        print(f"{len(without_history)} features have no history file, their fields are null (first few ids: {without_history[:10]})", file=sys.stderr)
    if orphan_files:
        print(f"{len(orphan_files)} history files match no feature (first few ids: {orphan_files[:10]})", file=sys.stderr)
    for name in fields:
        empty = sum(1 for f in collection["features"] if f["properties"].get(name) is None)
        print(f"{name}: {len(collection['features']) - empty} set, {empty} empty", file=sys.stderr)


if __name__ == "__main__":
    main()
