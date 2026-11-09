"""HealthEquity's investment history (.xlsx) and the workbook reader under it, on made-up rows."""

import io
import zipfile

import pandas as pd
import pytest

from household import ingest, inbox, xlsx
from household.parsers import ParseError, healthequity, identify

TITLE = "All Investment Transactions"
RANGE = "Date Range: 01/01/2001 to 06/01/2001"
HEADER = ["Date", "Fund", "Category", "Description", "Price", "Amount", "Shares", "Total Shares", "Total Value"]


def workbook(rows):
    """A minimal .xlsx: text cells as shared strings, numbers as numbers, the second title cell inline."""
    shared, sheet_rows = [], []
    for n, row in enumerate(rows, start=1):
        cells = []
        for i, v in enumerate(row):
            ref = f"{chr(65 + i)}{n}"
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{v}</v></c>')
            elif n == 1 and i == 1:
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t>{v}</t></is></c>')
            else:
                shared.append(v.replace("&", "&amp;"))
                cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
        sheet_rows.append(f'<row r="{n}">{"".join(cells)}</row>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/workbook.xml", "<workbook/>")
        z.writestr("xl/sharedStrings.xml", "<sst>" + "".join(f"<si><t>{s}</t></si>" for s in shared) + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet><sheetData>{''.join(sheet_rows)}</sheetData></worksheet>")
    return buf.getvalue()


AS_TEXT = [[TITLE, TITLE], [RANGE], HEADER,
           ["01/05/2001", "ACMEX", "Buy", "Investment: ACMEX", "$10.00", "$50.00", "5.000", "5.000", "$50.00"],  # leak-guard: allow (made up)
           ["02/05/2001", "ACMEX", "Buy", "Investment: ACMEX", "$12.50", "$25.00", "2.000", "7.000", "$87.50"],  # leak-guard: allow (made up)
           ["03/16/2001", "ACMEX", "Dividend", "Investment: ACMEX", "$12.00", "$6.00", "0.500", "7.500", "$90.00"]]  # leak-guard: allow (made up)
# the same rows as numbers, dates as Excel serials (a workbook may store either)
AS_NUMBERS = [[TITLE, TITLE], [RANGE], HEADER,
              [36896, "ACMEX", "Buy", "Investment: ACMEX", 10, 50, 5, 5, 50],
              [36927, "ACMEX", "Buy", "Investment: ACMEX", 12.5, 25, 2, 7, 87.5],
              [36966, "ACMEX", "Dividend", "Investment: ACMEX", 12, 6, 0.5, 7.5, 90]]
ACCOUNTS = pd.DataFrame([{"account_id": "a_hsa", "last4": ""}, {"account_id": "a_ira", "last4": ""}])


def test_a_workbook_reads_as_rows_of_text_shared_inline_and_numbers():
    rows = xlsx.rows(workbook(AS_NUMBERS))
    assert rows[0] == [TITLE, TITLE] and rows[2] == HEADER and rows[3][:3] == ["36896", "ACMEX", "Buy"]
    assert xlsx.to_csv(workbook([["Quill & Ink", 1.5]])) == "Quill & Ink,1.5\n"
    with pytest.raises(xlsx.NotAWorkbook):
        xlsx.rows(b"PK\x03\x04 not really")


@pytest.mark.parametrize("rows", [AS_TEXT, AS_NUMBERS], ids=["text", "numbers"])
def test_the_inbox_reads_the_download_as_buys_reinvested_dividends_and_the_last_total(rows):
    data = workbook(rows)
    text = inbox.to_text("a_hsa_healthequity.xlsx", "", data)
    assert identify(text) is healthequity
    r = ingest.finish_investment_rows(healthequity.parse(text), "a_hsa_healthequity.xlsx", ACCOUNTS)
    a = r.tables["investment_activity"]
    assert a[["date", "kind", "shares", "value", "amount"]].values.tolist() == [
        ["2001-01-05", "buy", 5, 50, 0], ["2001-02-05", "buy", 2, 25, 0], ["2001-03-16", "reinvest", 0.5, 6, 6]]
    assert set(a["account_id"]) == {"a_hsa"} and a["activity_id"].is_unique
    assert r.tables["holdings"][["as_of", "account_id", "symbol", "shares", "value"]].values.tolist() == [
        ["2001-03-16", "a_hsa", "ACMEX", 7.5, 90]]
    assert r.tables["prices"]["close"].tolist() == [10, 12.5, 12]


def test_a_row_that_breaks_the_running_share_count_refuses_the_file():
    rows = AS_TEXT[:4] + [["02/05/2001", "ACMEX", "Buy", "Investment: ACMEX", "$12.50", "$25.00", "2.000", "9.000", "$87.50"]]  # leak-guard: allow (made up)
    with pytest.raises(ParseError, match="ACMEX on 2001-02-05"):
        healthequity.parse(inbox.to_text("a.xlsx", "", workbook(rows)))
