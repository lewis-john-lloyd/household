"""Build the push guard's private denylist from exports of the ledger.

    python tools/denylist.py --words words.txt paystubs.csv transactions.json ... > ~/.config/household/denylist.txt

Tables are CSV files, or JSON files of the shape {"values": [[...], ...]} (what the Sheets
API returns for a range). Every cell of the tables that reads as an amount with cents (not a round dollar
figure, which a made-up test value could equal, and at least MIN_AMOUNT) goes
in as an amount; the words file (names, streets, cities, employers, account
digits: one per line) goes in as it is. The output lives outside the repo and
must never be committed.
"""

import argparse
import csv
import json
import re
import sys

MIN_AMOUNT = 100.0  # smaller amounts collide with made-up test values too often to be worth it
MONEY = re.compile(r"^\(?-?\$?-?[\d,]*\d\.\d\d\)?$")


def rows(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        if path.endswith(".json"):
            return json.load(f).get("values", [])
        return list(csv.reader(f))


def amounts(paths):
    out = set()
    for path in paths:
        for row in rows(path):
            for cell in row:
                text = str(cell).strip()
                if not MONEY.match(text):
                    continue
                value = abs(float(re.sub(r"[^\d.]", "", text)))
                if value >= MIN_AMOUNT and not text.endswith(".00"):
                    out.add(f"{value:.2f}")
    return out


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--words", help="file of words and phrases, one per line")
    parser.add_argument("csvs", nargs="*")
    args = parser.parse_args(argv)
    lines = ["# household denylist for tools/push_guard.py; never commit this file"]
    if args.words:
        with open(args.words, encoding="utf-8") as f:
            lines += sorted({w.strip() for w in f if w.strip() and not w.startswith("#")})
    lines += sorted(amounts(args.csvs), key=float)
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
