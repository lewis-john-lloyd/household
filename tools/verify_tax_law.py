"""Check every tax-law citation in household.taxdata against the source PDFs.

Run before a release that changes tax law (each January):

    python tools/verify_tax_law.py DIR

DIR holds the source PDFs (any names). Each is identified the way ingest does,
and its SHA-256 is compared with the one the citations were checked against;
a different revision is reported, since its page numbers may have moved. Then
every citation is checked (taxsources.check). Exit status is 1 if any document
is missing or any citation fails.
"""

import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pypdf import PdfReader  # noqa: E402

from household import pdftext, taxdata, taxsources  # noqa: E402
from household.parsers import tax_law  # noqa: E402


def load(folder):
    """{label: page texts} for the recognized PDFs in folder, plus hash notes."""
    pages, notes = {}, []
    for path in sorted(Path(folder).glob("*.pdf")):
        data = path.read_bytes()
        text = pdftext.extract(data)
        if not tax_law.sniff(text):
            notes.append(f"skipped {path.name}: not a recognized tax law source")
            continue
        label = tax_law.parse(text).label
        sha = hashlib.sha256(data).hexdigest()[:16]
        expected = taxdata.DOCUMENTS.get(label, (None, None))[1]
        if expected is None:
            notes.append(f"{path.name}: {label} is not cited by taxdata")
        elif sha != expected:
            notes.append(f"{path.name}: {label} is a different revision ({sha}, cited {expected})")
        pages[label] = [page.extract_text() or "" for page in PdfReader(path).pages]
    return pages, notes


def main(argv):
    if len(argv) != 1:
        print(__doc__)
        return 2
    pages, notes = load(argv[0])
    for note in notes:
        print(note)
    missing = sorted(set(taxdata.DOCUMENTS) - set(pages))
    for label in missing:
        print(f"missing: {label} ({taxdata.DOCUMENTS[label][0]})")
    result = taxsources.check(pages)
    for r in result.to_dict("records"):
        year = r["year"] if r["year"] != "" else "rule"
        mark = "ok  " if r["ok"] else "FAIL"
        print(f"{mark} {year} {r['item']}: {r['document']} p{r['page']}"
              + ("" if r["ok"] else f"  ({r['detail']})"))
    gaps = taxsources.uncovered()
    for year, item in gaps:
        print(f"FAIL {year} {item}: no citation and no reason in UNCITED")
    failed = int((~result["ok"]).sum()) + len(gaps) + len(missing)
    print(f"\n{len(result) - int((~result['ok']).sum())}/{len(result)} citations hold; "
          f"{len(missing)} documents missing; {len(gaps)} values uncovered")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
