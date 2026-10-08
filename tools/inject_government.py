"""Add a government type to every province's nation.

CK2 does not store government in the history files: the game picks it from the ruler's
capital holding (castle, city, temple, tribal) and then checks each government's
`potential` trigger in common/governments. This script redoes that pick for the ruler of
each nation on the nation date:

  1. the ruler is the holder of the nation's top title (history/titles)
  2. the ruler's religion, culture and sex come from history/characters
  3. the capital province is the top title's `capital =` in landed_titles (or the first
     capital found below it), and its capital holding is the first holding in that county's province history file
  3b. a holder of a title with `historical_nomad = yes` (history/titles) is Nomadic
  4. governments whose preferred_holdings include that holding type are tried in file
     order, and the first whose potential trigger is not false wins

Triggers the history files cannot answer (controls_religion, religion features, who is a
patrician, ...) count as "unknown": a government that only depends on unknowns can still
win, but is reported as uncertain in `government_certain`.

Usage (from the tools folder), after inject_nations.py:
  python inject_government.py --geojson provinces_nations.geojson --common path/to/common \\
      --history path/to/history --localisation path/to/localisation --out provinces_gov.geojson
"""
import argparse
import csv
import json
import re
import sys
from pathlib import Path

import inject_history as ih
import inject_nations as inn

TIERS = {"b": 0, "c": 1, "d": 2, "k": 3, "e": 4}
TIER_NAMES = {"baron": 0, "count": 1, "duke": 2, "king": 3, "emperor": 4}
HOLDING_FOR = {"castle": "CASTLE", "city": "CITY", "temple": "TEMPLE", "tribal": "TRIBAL"}
FALLBACK = {
    "CASTLE": "feudal_government",
    "CITY": "republic_government",
    "TEMPLE": "theocracy_government",
    "TRIBAL": "tribal_government",
}
TITLE_KEY = re.compile(r"^[bcdke]_")


def pairs_of(path):
    pairs, _ = ih.parse_block(ih.tokenize(ih.read_text(path)), 0)
    return pairs


# ---------------------------------------------------------------- landed titles


def load_title_tree(folder):
    """{title: {"capital": province id or None, "children": [titles in file order]}}"""
    tree = {}

    def walk(pairs, parent):
        for key, value in pairs:
            if key and TITLE_KEY.match(key) and isinstance(value, list):
                capital = None
                head_of = None
                for k, v in value:
                    if k == "capital" and isinstance(v, str) and v.isdigit():
                        capital = int(v)
                    elif k == "controls_religion" and isinstance(v, str):
                        head_of = v  # holding this title means controlling that religion
                tree[key] = {"capital": capital, "children": [], "head_of": head_of}
                if parent is not None:
                    tree[parent]["children"].append(key)
                walk(value, key)

    for path in sorted(folder.glob("*.txt")):
        walk(pairs_of(path), None)
    return tree


def first_capital(tree, title, seen=None):
    seen = seen or set()
    if title in seen or title not in tree:
        return None
    seen.add(title)
    if tree[title]["capital"]:
        return tree[title]["capital"]
    for child in tree[title]["children"]:
        found = first_capital(tree, child, seen)
        if found:
            return found
    return None


# ------------------------------------------------------------- province history


def load_province_holdings(folder):
    """{province id: (county title, [(barony, holding type), ...] in file order)}"""
    out = {}
    for path in sorted(folder.glob("*.txt")):
        match = re.match(r"\s*(\d+)", path.name)
        if not match:
            continue
        county = None
        baronies = []
        for key, value in pairs_of(path):
            if key == "title" and isinstance(value, str):
                county = value
            elif key and key.startswith("b_") and isinstance(value, str) and value in HOLDING_FOR:
                baronies.append((key, value))
        out[int(match.group(1))] = (county, baronies)
    return out


def capital_holding(tree, holdings, province_of_county, county):
    """Holding type of the county's capital: the first holding listed in the province history file
    (checked against the game: the Holy Columbian Confederacy is Feudal because Charleston lists a castle first)."""
    province = province_of_county.get(county)
    if province is None:
        return None
    _, baronies = holdings[province]
    types = dict(baronies)
    return baronies[0][1] if baronies else None


# -------------------------------------------------------------------- religions


def load_religion_groups(folder):
    groups = {}
    for path in sorted(folder.glob("*.txt")):
        for group, block in pairs_of(path):
            if not group or not isinstance(block, list):
                continue
            for name, inner in block:
                if name and isinstance(inner, list) and any(k == "color" for k, _ in inner):
                    groups[name] = group
    return groups


# ------------------------------------------------------------ cultures, regions


def load_culture_groups(folder):
    """{culture: culture group} from common/cultures (a culture has male_names or color)."""
    groups = {}
    for path in sorted(folder.glob("*.txt")):
        for group, block in pairs_of(path):
            if not group or not isinstance(block, list):
                continue
            for name, inner in block:
                if name and isinstance(inner, list) and any(k in ("male_names", "color") for k, _ in inner):
                    groups[name] = group
    return groups


def load_regions(path, tree, province_of_county):
    """{region: set of province ids} from map/geographical_region.txt (duchies, counties, provinces, sub-regions)."""
    regions = {}
    for name, block in pairs_of(path):
        if not name or not isinstance(block, list):
            continue
        provinces = set()
        for key, value in block:
            items = [v for k, v in value if k is None and isinstance(v, str)] if isinstance(value, list) else []
            if key == "provinces":
                provinces.update(int(v) for v in items if v.isdigit())
            elif key == "counties":
                provinces.update(province_of_county[v] for v in items if v in province_of_county)
            elif key == "duchies":
                for duchy in items:
                    for county in tree.get(duchy, {}).get("children", []):
                        if county in province_of_county:
                            provinces.add(province_of_county[county])
            elif key == "regions":
                for sub in items:
                    provinces |= regions.get(sub, set())
        regions[name] = provinces
    return regions


# ------------------------------------------------------------------- characters


def load_rulers(folder, holders, cutoff):
    """{character id: {"religion", "culture", "female"}} for the ids in `holders`."""
    rulers = {}
    for path in sorted(folder.glob("*.txt")):
        for key, block in pairs_of(path):
            if key in holders and isinstance(block, list):
                rulers[key] = {
                    "religion": inn.latest(block, "religion", cutoff),
                    "culture": inn.latest(block, "culture", cutoff),
                    "female": any(k == "female" and v == "yes" for k, v in block),
                }
    return rulers


# ------------------------------------------------------------------- governments


def load_governments(folder):
    """[(name, definition pairs)] in file order, files sorted by name."""
    out = []
    for path in sorted(folder.glob("*.txt")):
        for _, block in pairs_of(path):
            if isinstance(block, list):
                for name, definition in block:
                    if name and isinstance(definition, list):
                        out.append((name, definition))
    return out


def get(block, key):
    for k, v in block:
        if k == key:
            return v
    return None


class Evaluator:
    """Three-valued trigger evaluation: True, False, or None when it cannot be known."""

    def __init__(self, governments, groups, ctx):
        self.defs = dict(governments)
        self.groups = groups
        self.ctx = ctx
        self.active = set()

    def potential(self, name):
        definition = self.defs.get(name)
        if definition is None:
            return None
        block = get(definition, "potential")
        if not isinstance(block, list):
            return True
        if name in self.active:
            return None
        self.active.add(name)
        try:
            return self.block(block, "AND")
        finally:
            self.active.discard(name)

    @staticmethod
    def land(values):
        if any(v is False for v in values):
            return False
        return None if any(v is None for v in values) else True

    @staticmethod
    def lor(values):
        if any(v is True for v in values):
            return True
        return None if any(v is None for v in values) else False

    @staticmethod
    def lnot(value):
        return None if value is None else not value

    def block(self, pairs, mode):
        values = [self.trigger(k, v) for k, v in pairs if k]
        if mode == "AND":
            return self.land(values)
        if mode == "OR":
            return self.lor(values)
        if mode == "NOR":
            return self.lnot(self.lor(values))
        return self.lnot(self.land(values))  # NAND

    @staticmethod
    def boolean(known, value):
        """`known` is the true state (True/False/None); `value` is the yes/no in the script."""
        if known is None:
            return None
        return known == (value == "yes")

    def trigger(self, key, value):
        ctx = self.ctx
        if key in ("AND", "OR", "NOR", "NAND") and isinstance(value, list):
            return self.block(value, key)
        if key == "NOT" and isinstance(value, list):
            return self.block(value, "NOR")
        if key == "religion":
            return None if ctx["religion"] is None else ctx["religion"] == value
        if key == "religion_group":
            group = self.groups.get(ctx["religion"])
            return None if group is None else group == value
        if key == "culture":
            return None if ctx["culture"] is None else ctx["culture"] == value
        if key == "culture_group":
            group = ctx["culture_groups"].get(ctx["culture"])
            return None if group is None else group == value
        if key == "region":
            return None if value not in ctx["regions"] or ctx["capital_province"] is None else ctx["capital_province"] in ctx["regions"][value]
        if key == "has_title_flag":
            return False  # title flags are only set by in-game events and decisions, never at game start
        if key == "is_theocracy":
            return self.boolean(ctx["theocracy"], value)
        if key == "is_government_potential":
            return self.potential(value)
        if key == "tier":
            return ctx["tier"] == TIER_NAMES.get(str(value).lower())
        if key in ("higher_tier_than", "higher_real_tier_than"):
            return ctx["tier"] > TIER_NAMES.get(str(value).lower(), 99)
        if key == "has_landed_title":
            return value in ctx["titles"]
        if key == "primary_title" and isinstance(value, list):
            return self.block(value, "AND")
        if key == "top_liege" and isinstance(value, list):
            # a nation's ruler is their own top liege; a vassal's top liege is the nation's ruler
            outer = self.ctx
            self.ctx = dict(outer, top=outer["nation_top"], vassal=False)
            try:
                return self.block(value, "AND")
            finally:
                self.ctx = outer
        if key == "any_liege":
            return ctx["vassal"] and self.block(value, "AND") if isinstance(value, list) else ctx["vassal"]
        if key == "title":
            return value == ctx["top"]
        if key == "capital_scope" and isinstance(value, list):
            return self.block(value, "AND")
        if key == "port":
            return self.boolean(ctx["port"], value)
        if key == "controls_religion":
            return self.boolean(ctx["controls"], value)
        if key == "is_female":
            return self.boolean(ctx["female"], value)
        if key == "always":
            return value == "yes"
        if key == "any_demesne_province":
            return True
        if key == "has_game_started":
            return value == "no"  # history is read as the game starts
        if key == "is_save_game":
            return value == "no"
        if key in ("is_patrician", "holy_order", "mercenary", "is_offmap_tag"):
            return value == "no" if key != "is_offmap_tag" else False
        # has_religion_feature(s), is_merchant_republic, is_feudal,
        # liege_before_war, has_law, holding_type, ... cannot be told from the history files
        return None


# ---------------------------------------------------------------------- the rest


def hex_color(block):
    numbers = [v for k, v in (block or []) if k is None and isinstance(v, str)]
    try:
        values = [float(n) for n in numbers[:3]]
    except ValueError:
        return None
    if len(values) < 3:
        return None
    if any("." in n for n in numbers[:3]):
        values = [v * 255 for v in values]
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v))) for v in values)


def load_government_names(folder):
    names = {}
    for path in sorted(folder.glob("*.csv")):
        with open(path, encoding="cp1252", errors="replace", newline="") as handle:
            for row in csv.reader(handle, delimiter=";"):
                if len(row) >= 2 and row[0].strip().endswith("government") and row[0].strip() not in names and row[1].strip():
                    names[row[0].strip()] = row[1].strip()
                elif len(row) >= 2 and row[0].strip() == "military_governorate" and row[1].strip():
                    names.setdefault("military_governorate", row[1].strip())
    return names


def clean_government_key(key):
    words = re.sub(r"_government$", "", key).replace("_", " ").split()
    return " ".join(w[:1].upper() + w[1:] for w in words) or key


def sea_vertices(features):
    seen = set()
    for feature in features:
        if feature["properties"].get("kind") != "sea":
            continue
        for ring in rings(feature["geometry"]):
            seen.update((round(x, 2), round(y, 2)) for x, y in ring)
    return seen


def rings(geometry):
    if geometry["type"] == "Polygon":
        return geometry["coordinates"]
    if geometry["type"] == "MultiPolygon":
        return [ring for polygon in geometry["coordinates"] for ring in polygon]
    return []


def is_port(feature, sea):
    return any((round(x, 2), round(y, 2)) in sea for ring in rings(feature["geometry"]) for x, y in ring)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--geojson", default="provinces_nations.geojson", help="output of inject_nations.py")
    parser.add_argument("--common", default="common")
    parser.add_argument("--history", default="history")
    parser.add_argument("--localisation", default="localisation")
    parser.add_argument("--regions", default=None, help="path to map/geographical_region.txt (needed for region triggers such as Open Range Feudalism)")
    parser.add_argument("--out", default="provinces_gov.geojson")
    args = parser.parse_args()

    common, history = Path(args.common), Path(args.history)
    for folder in (common / "governments", common / "landed_titles", common / "religions", history / "titles", history / "provinces", history / "characters"):
        if not folder.is_dir():
            sys.exit(f"{folder} is not a directory")

    with open(args.geojson, encoding="utf-8") as handle:
        collection = json.load(handle)
    date = collection.get("nation_date")
    cutoff = ih.parse_date(date) if date else None
    if cutoff is None:
        sys.exit("the geojson has no nation_date; run inject_nations.py first")

    tree = load_title_tree(common / "landed_titles")
    holdings = load_province_holdings(history / "provinces")
    province_of_county = {county: pid for pid, (county, _) in holdings.items() if county}
    groups = load_religion_groups(common / "religions")
    culture_groups = load_culture_groups(common / "cultures") if (common / "cultures").is_dir() else {}
    region_path = Path(args.regions) if args.regions else history.parent / "map" / "geographical_region.txt"
    regions = load_regions(region_path, tree, province_of_county) if region_path.is_file() else {}
    governments = load_governments(common / "governments")
    names = load_government_names(Path(args.localisation))
    titles = inn.load_titles(history / "titles", cutoff)
    held_by = {}
    for key, entry in titles.items():
        if inn.is_held(titles, key):
            held_by.setdefault(entry["holder"], set()).add(key)

    nomad_titles = set()
    for path in sorted((history / "titles").glob("*.txt")):
        if inn.latest(pairs_of(path), "historical_nomad", cutoff) == "yes":
            nomad_titles.add(path.stem)

    nomad_titles_all = nomad_titles
    feature_list = collection["features"]
    counties_of = {}
    for feature in feature_list:
        top, county = feature["properties"].get("nation_title"), feature["properties"].get("title")
        if top and county:
            counties_of.setdefault(top, []).append(county)

    realms = {}  # realm title -> nation top title (nations and their direct vassals)
    for feature in feature_list:
        properties = feature["properties"]
        top = properties.get("nation_title")
        if not top:
            continue
        realms.setdefault(top, top)
        vassal = properties.get("vassal_title")
        if vassal and vassal in titles:
            realms.setdefault(vassal, top)
        elif vassal:
            realms.setdefault(vassal, top)

    holders_needed = {titles[t]["holder"] for t in realms if t in titles}
    rulers = load_rulers(history / "characters", holders_needed, cutoff)
    sea = sea_vertices(feature_list)
    feature_of = {f["properties"]["id"]: f for f in feature_list}
    colors = {name: hex_color(get(definition, "color")) for name, definition in governments}
    notes = {"no ruler data": 0, "no capital holding": 0, "uncertain": 0}

    def decide(realm, nation_top):
        """(government, certain, religion, culture) for the holder of `realm`."""
        entry = titles.get(realm)
        if entry is None:
            return None
        holder = entry["holder"]
        ruler = rulers.get(holder)
        religion = ruler["religion"] if ruler else None
        culture = ruler["culture"] if ruler else None
        own_titles = held_by.get(holder, {realm})
        if nomad_titles_all & set(own_titles):
            return ("nomadic_government", True, religion, culture)
        capital = first_capital(tree, realm)
        county = (holdings.get(capital) or (None,))[0] if capital else None
        if county is None and realm[0] == "c":
            county = realm
        if county is None:
            own = [c for c in counties_of.get(nation_top, []) if titles.get(c, {}).get("holder") == holder]
            county = (own or counties_of.get(nation_top, [None]))[0]
        holding = capital_holding(tree, holdings, province_of_county, county) if county else None
        if ruler is None:
            notes["no ruler data"] += 1
        if holding is None:
            notes["no capital holding"] += 1
            return (None, False, religion, culture)
        capital_province = province_of_county.get(county)
        port = is_port(feature_of[capital_province], sea) if capital_province in feature_of else None
        controls = None if religion is None else any(tree.get(t, {}).get("head_of") == religion for t in own_titles)
        tier = max((TIERS[t[0]] for t in own_titles if t[0] in TIERS), default=TIERS[realm[0]])
        ctx = {
            "controls": controls,
            "religion": religion,
            "culture": culture,
            "female": ruler["female"] if ruler else None,
            "tier": tier,
            "titles": own_titles,
            "top": max(own_titles, key=lambda t: (TIERS.get(t[0], -1), t == realm)) if own_titles else realm,
            "nation_top": nation_top,
            "vassal": realm != nation_top,
            "port": port,
            "capital_province": capital_province,
            "culture_groups": culture_groups,
            "regions": regions,
            "theocracy": holding == "TEMPLE",
        }
        evaluator = Evaluator(governments, groups, ctx)
        wanted = HOLDING_FOR[holding]
        order = [name for name, definition in governments if wanted in [v for k, v in (get(definition, "preferred_holdings") or [])]]
        verdicts = [(name, evaluator.potential(name)) for name in order]
        pick = next((name for name, ok in verdicts if ok is True), None)
        certain = pick is not None
        if pick is None:
            pick = next((name for name, ok in verdicts if ok is None), None)
        if pick is None:
            pick = FALLBACK[wanted]
        if not certain:
            notes["uncertain"] += 1
        return (pick, certain, religion, culture)

    result = {}
    for realm, top in sorted(realms.items()):
        found = decide(realm, top)
        if found:
            result[realm] = found

    def fill(properties, prefix, realm):
        found = result.get(realm)
        pick = found[0] if found else None
        properties[prefix + "government"] = pick
        properties[prefix + "government_name"] = (names.get(pick) or clean_government_key(pick)) if pick else None
        properties[prefix + "government_color"] = colors.get(pick) if pick else None
        properties[prefix + "government_certain"] = found[1] if found else None
        return pick

    counted = {}
    for feature in feature_list:
        properties = feature["properties"]
        for key in ("government", "government_name", "government_color", "government_certain",
                    "holder_government_name", "holder_government_color", "holder_government_certain",
                    "ruler_religion", "ruler_culture", "vassal_religion", "vassal_culture"):
            properties[key] = None
        top = properties.get("nation_title")
        if top not in result or result[top][0] is None:
            continue
        pick = fill(properties, "", top)
        counted[pick] = counted.get(pick, 0) + 1
        properties["ruler_religion"], properties["ruler_culture"] = result[top][2], result[top][3]
        vassal = properties.get("vassal_title") or top
        found = result.get(vassal) or result[top]
        fill(properties, "holder_", vassal if vassal in result else top)
        properties["vassal_religion"], properties["vassal_culture"] = found[2], found[3]

    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, separators=(",", ":"))

    by_nation = {}
    for top in {f["properties"].get("nation_title") for f in feature_list}:
        if top in result and result[top][0]:
            by_nation[result[top][0]] = by_nation.get(result[top][0], 0) + 1
    print(f"governments for {len(by_nation and sum(by_nation.values()) and result)} realms (nations and direct vassals) on {date}")
    print("nations: " + ", ".join(f"{n} {g}" for g, n in sorted(by_nation.items(), key=lambda i: -i[1])))
    print("provinces: " + ", ".join(f"{n} {g}" for g, n in sorted(counted.items(), key=lambda i: -i[1])))
    print(f"{notes['uncertain']} realms decided with unknown triggers, {notes['no ruler data']} rulers not found in history/characters, {notes['no capital holding']} realms with no capital holding")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
