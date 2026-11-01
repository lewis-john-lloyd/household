"""Text from a PDF with its layout kept, so columns stay at stable positions.

pypdf's layout mode does this for most PDFs. Some (ADP's earnings statements)
carry a barcode font of tiny glyphs placed far off the page, which wrecks
layout mode's character grid, and their numbers come as separate pieces set at
fixed positions (dollars, then cents at the decimal column, no decimal point).
For a page whose layout text comes out with NUL characters, the page is drawn
again from its text pieces: each piece goes on the line of its baseline, at
the column of its x position (POINTS points to a column), and pieces in tiny
type are dropped. A piece starts at the same column on every page of a layout,
so a parser can tell columns apart by position.
"""

import io

MAX_PAGES = 5  # every supported layout fits; long documents are identified from their first pages
POINTS = 2.0  # points per column when a page is drawn from its pieces
MIN_SIZE = 2.0  # type smaller than this (points) is a barcode, not text
ROW_TOLERANCE = 1.5  # pieces whose baselines are this close (points) share a line


def extract(data, max_pages=MAX_PAGES):
    """Layout text of the first max_pages pages (all pages when max_pages is None)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = reader.pages if max_pages is None else reader.pages[:max_pages]
    return "\n".join(_page_text(page) for page in pages)


def _page_text(page):
    text = page.extract_text(extraction_mode="layout")
    return render(fragments(page)) if "\x00" in text else text


def fragments(page):
    """(y, x, text) for each piece of text on the page, in points, tiny type left out."""
    out = []

    def visit(text, cm, tm, font, size):
        piece = text.replace("\x00", "").rstrip("\n")
        scale = abs(tm[0] * (size or 1.0))
        if piece.strip() and scale >= MIN_SIZE:
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            out.append((y, x, piece))

    page.extract_text(visitor_text=visit)
    return out


def render(pieces, points=POINTS):
    """Lines of text from (y, x, text) pieces: top to bottom, each piece at the column of its x."""
    rows = []
    for y, x, text in sorted(pieces, key=lambda p: (-p[0], p[1])):
        if rows and abs(rows[-1][0] - y) <= ROW_TOLERANCE:
            rows[-1][1].append((x, text))
        else:
            rows.append([y, [(x, text)]])
    lines = []
    for _, row in rows:
        line = ""
        for x, text in sorted(row):
            col = int(round(x / points))
            line = line + " " if len(line) >= col else line.ljust(col)
            line += text.strip()
        lines.append(line.rstrip())
    return "\n".join(lines)
