"""Schwab Equity Award Center transaction export (CSV).

Each action row (Deposit, Sale, Dividend, Journal) is followed by detail rows
with an empty Date. A Deposit's detail gives the award, vest date and vest-day
market value; a Sale's details are its lots with cost basis, gain and holding
period. Exports overlap, so rows are keyed on what identifies them, not on the
file.

Check: each sale's lots must add to its share count, and lot proceeds (basis
plus gain) to the sale amount plus fees.
"""

import csv
import io

import pandas as pd

from .common import ParseError, Result, us_date
from ..legacy import parse_money

NAME = "schwab_equity"
REQUIRED = {"Date", "Action", "Quantity", "AwardId", "VestDate", "VestFairMarketValue",
            "TotalCostBasis", "RealizedGainLoss", "HoldingPeriod", "GrantId"}


def sniff(text):
    first = text.lstrip("﻿").splitlines()[0] if text.strip() else ""
    return all(f'"{c}"' in first or c in first.split(",") for c in REQUIRED)


def _num(value):
    return parse_money(value) if str(value).strip() else 0.0


def parse(text):
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    vests, sales, current, lots = [], [], None, []
    dividends = 0

    def close_sale():
        if current is None or current["Action"] != "Sale":
            return
        shares = sum(l["shares"] for l in lots)
        proceeds = sum(l["basis"] + l["gain"] for l in lots)
        amount = _num(current["Amount"]) + _num(current["FeesAndCommissions"])
        if abs(shares - _num(current["Quantity"])) > 0.0005:
            raise ParseError(f"{NAME} {current['Date']}: lots {shares} != sale {current['Quantity']}")
        if abs(proceeds - amount) > 0.05:
            raise ParseError(f"{NAME} {current['Date']}: lots {proceeds:.2f} != sale {amount:.2f}")
        sales.extend(lots)

    for row in rows:
        if row["Date"]:
            close_sale()
            current, lots = row, []
            if row["Action"] == "Dividend":
                dividends += 1
            continue
        if current is None:
            raise ParseError(f"{NAME}: detail row before any action")
        if current["Action"] == "Deposit" and row["VestDate"]:
            vests.append({
                "vest_date": us_date(row["VestDate"]),
                "grant_id": row["AwardId"],
                "award_date": us_date(row["AwardDate"]) if row.get("AwardDate") else "",
                "net_shares": _num(current["Quantity"]),
                "fmv": _num(row["VestFairMarketValue"]),
                "deposit_date": us_date(current["Date"]),
            })
        elif current["Action"] == "Sale" and row["GrantId"]:
            shares = _num(row["Shares"])
            lots.append({
                "sale_date": us_date(current["Date"]),
                "grant_id": row["GrantId"],
                "vest_date": us_date(row["VestDate"]),
                "shares": shares,
                "sale_price": _num(row["SalePrice"]),
                "basis": round(_num(row["TotalCostBasis"]), 4),
                "gain": round(_num(row["RealizedGainLoss"]), 4),
                "term": row["HoldingPeriod"].strip().lower().replace(" ", "_"),
            })
    close_sale()

    v, s = pd.DataFrame(vests), pd.DataFrame(sales)
    if not s.empty:
        s["proceeds"] = (s["basis"] + s["gain"]).round(4)
    dates = sorted(list(v.get("vest_date", [])) + list(s.get("sale_date", [])))
    return Result(kind="equity_export", parser=NAME,
                  label=f"schwab_equity_{dates[-1] if dates else 'empty'}",
                  covers_from=dates[0] if dates else "", covers_to=dates[-1] if dates else "",
                  tables={"equity_vests": v, "equity_sales": s},
                  keys={"equity_vests": ["vest_date", "grant_id"],
                        "equity_sales": ["sale_date", "grant_id", "vest_date", "shares", "basis"]},
                  notes=[f"{dividends} dividend rows not ingested"] if dividends else [])
