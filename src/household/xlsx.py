"""The first worksheet of an Excel workbook (.xlsx) as rows of text, read with the standard library only.

An .xlsx is a zip of XML parts: the cells are in xl/worksheets/sheet1.xml (the first sheet, as the workbook
lists it), and a cell of text usually points into xl/sharedStrings.xml. Each cell comes back as text: a
shared or inline string as it is, a number as Excel stores it (a date stays a serial day count; the parser
that reads the rows knows which columns hold dates). The inbox turns a workbook into CSV text this way, so
the parsers read it like any CSV download.

Limits like the inbox's for a zip: a workbook part may not unpack past MAX_PART bytes.
"""

import csv
import io
import re
import zipfile

MAX_PART = 20 * 1024 * 1024
ENTITIES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
ENTITY = re.compile(r"&(#x[0-9a-fA-F]+|#\d+|[a-zA-Z]+);")
CELL = re.compile(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.S)
ROW = re.compile(r"<row\b[^>]*?(?:/>|>(.*?)</row>)", re.S)


class NotAWorkbook(ValueError):
    pass


def unescape(text):
    def one(m):
        name = m.group(1)
        if name.startswith("#x"):
            return chr(int(name[2:], 16))
        if name.startswith("#"):
            return chr(int(name[1:]))
        return ENTITIES.get(name, m.group(0))

    return ENTITY.sub(one, text)


def _text(fragment):
    """The text of an <si> or <is> element: its <t> runs joined (rich text has several)."""
    return unescape("".join(re.findall(r"<t\b[^>]*>(.*?)</t>", fragment, re.S)))


def _column(ref):
    letters = re.match(r"[A-Z]+", ref or "")
    n = 0
    for ch in letters.group() if letters else "":
        n = n * 26 + ord(ch) - 64
    return n - 1


def _part(z, name):
    info = z.getinfo(name)
    if info.file_size > MAX_PART:
        raise NotAWorkbook(f"workbook part {name} unpacks to {info.file_size} bytes, more than {MAX_PART}")
    return z.read(name).decode("utf-8")


def rows(data):
    """The first worksheet's rows, each a list of cell texts (blank cells in between kept as "")."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise NotAWorkbook("not a zip") from e
    with z:
        names = set(z.namelist())
        sheets = sorted(
            (n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)),
            key=lambda n: int(re.search(r"(\d+)\.xml$", n).group(1)),
        )
        if not sheets:
            raise NotAWorkbook("no worksheet in the workbook")
        shared = []
        if "xl/sharedStrings.xml" in names:
            shared = [_text(si) for si in re.findall(r"<si\b[^>]*>(.*?)</si>", _part(z, "xl/sharedStrings.xml"), re.S)]
        sheet = _part(z, sheets[0])
    out = []
    for row in ROW.finditer(sheet):
        cells = {}
        for c in CELL.finditer(row.group(1) or ""):
            attrs, body = c.group(1), c.group(2) or ""
            ref = re.search(r'\br="([A-Z]+\d+)"', attrs)
            kind = re.search(r'\bt="(\w+)"', attrs)
            kind = kind.group(1) if kind else "n"
            value = re.search(r"<v>(.*?)</v>", body, re.S)
            if kind == "s" and value:
                text = shared[int(value.group(1))]
            elif kind == "inlineStr":
                text = _text(body)
            else:
                text = unescape(value.group(1)) if value else ""
            at = _column(ref.group(1)) if ref else len(cells)
            cells[at] = text
        width = max(cells) + 1 if cells else 0
        out.append([cells.get(i, "") for i in range(width)])
    return out


def to_csv(data):
    """The first worksheet as CSV text."""
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows(data))
    return buf.getvalue()
