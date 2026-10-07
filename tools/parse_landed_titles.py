"""Extract the official `color = { R G B }` of every title in CK2's landed_titles.txt.

Usage:
    python parse_landed_titles.py path/to/common/landed_titles/landed_titles.txt [--out title_colors.json]

The file is deeply nested (empire > kingdom > duchy > county > barony), so a plain
"find every color block" regex would hand a kingdom's color to whichever title
happened to be listed above it. Instead one regex tokenises the text into
title openers, color blocks and braces, and a stack tracks which title a color
block directly belongs to. color2 (the secondary color) is ignored.
"""
import argparse
import json
import re
import sys
from pathlib import Path

TOKEN = re.compile(
    r"""
    (?P<title>\b[bcdke]_[^\s=#{}"]+)\s*=\s*\{                       # k_california = {
  | (?P<color>\bcolor\s*=\s*\{\s*
        (?P<r>\d+(?:\.\d+)?)\s+(?P<g>\d+(?:\.\d+)?)\s+(?P<b>\d+(?:\.\d+)?)
        \s*\})                                                      # color = { 12 34 56 }
  | (?P<open>\{)
  | (?P<close>\})
    """,
    re.VERBOSE,
)


def read_text(path):
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    return raw.decode("cp1252", errors="replace")


def clean(text):
    text = re.sub(r'"[^"]*"', '""', text)  # quoted strings may contain braces or '#'
    return re.sub(r"#[^\n]*", "", text)    # comments


def to_hex(r, g, b):
    # CK2 uses 0-255 integers; clamp anything odd instead of producing an invalid hex code
    channels = [max(0, min(255, int(round(float(v))))) for v in (r, g, b)]
    return "#{:02x}{:02x}{:02x}".format(*channels)


def parse_text(text):
    colors = {}
    stack = []  # one entry per open brace: a title key, or None for a non-title block
    for match in TOKEN.finditer(clean(text)):
        if match.group("title"):
            stack.append(match.group("title"))
        elif match.group("open"):
            stack.append(None)
        elif match.group("close"):
            if stack:
                stack.pop()
        elif match.group("color"):
            # only counts if the block we are directly inside is a title block
            owner = stack[-1] if stack else None
            if owner and owner not in colors:
                colors[owner] = to_hex(match.group("r"), match.group("g"), match.group("b"))
    return colors


def parse_landed_titles(path):
    """Return {title_key: '#rrggbb'} for a landed_titles file, or every .txt in a folder."""
    path = Path(path)
    files = sorted(path.glob("*.txt")) if path.is_dir() else [path]
    colors = {}
    for file in files:
        for key, value in parse_text(read_text(file)).items():
            colors.setdefault(key, value)
    return colors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("landed_titles")
    parser.add_argument("--out", default=None, help="optional JSON dump of {title: hex}")
    args = parser.parse_args()
    colors = parse_landed_titles(args.landed_titles)
    if not colors:
        sys.exit("no title colors found - is this landed_titles.txt?")
    print(f"found {len(colors)} titles with a color block")
    if args.out:
        Path(args.out).write_text(json.dumps(colors, indent=1), encoding="utf-8")
        print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
