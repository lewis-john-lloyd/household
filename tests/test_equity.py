"""The Equity page's tables, from made-up grants: what is granted, vested and still to vest, its value, and the
shares held."""

import pandas as pd

from household import equity, schema


def schedule(*rows):
    cols = list(schema.table(schema.LEDGER, "vest_schedule").columns)
    return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)


TODAY = "2026-11-13"
# an RSU vesting 10 shares a quarter for two years, and an option over 20 shares at 40 a share in two tranches
SCHED = schedule(
    *[
        ("g1", "2025-02-25", "ACME", "RSU", "", "", f"{d}", "10", "d1")
        for d in (
            "2025-05-25",
            "2025-08-25",
            "2025-11-25",
            "2026-02-25",
            "2026-05-25",
            "2026-08-25",
            "2026-11-25",
            "2027-02-25",
        )
    ],
    ("o1", "2026-01-10", "ACME", "OPTION", "40", "2036-01-10", "2026-07-10", "10", "d2"),
    ("o1", "2026-01-10", "ACME", "OPTION", "40", "2036-01-10", "2027-07-10", "10", "d2"),
)
PRICES = pd.DataFrame([{"symbol": "ACME", "date": "2026-11-12", "priced_date": "", "close": "50", "source": "x"}])
VESTED = pd.DataFrame(
    [
        {
            "vest_date": "2026-08-25",
            "grant_id": "g1",
            "award_date": "",
            "net_shares": "6",
            "fmv": "45",
            "deposit_date": "",
        }
    ]
)
LOTS = pd.DataFrame(
    [{"symbol": "ACME", "shares": "30", "basis": "1200"}, {"symbol": "WILLOW", "shares": "5", "basis": "10"}]
)


def test_each_grant_says_what_it_granted_vested_and_has_still_to_vest():
    g = equity.grants(SCHED, None, TODAY).set_index("grant_id")
    assert g.loc["g1", ["granted", "vested", "unvested", "next_vest", "last_vest"]].tolist() == [
        80.0,
        60.0,
        20.0,
        "2026-11-25",
        "2027-02-25",
    ]
    assert g.loc["g1", "grant_price"] == "" and g.loc["g1", "price_from"] == ""  # the award page gave none
    assert g.loc["o1", ["award_type", "grant_price", "price_from", "unvested"]].tolist() == [
        "OPTION",
        40.0,
        "award page",
        10.0,
    ]


def test_a_grant_price_set_for_a_grant_stands_in_for_the_award_pages():
    params = pd.DataFrame(
        [{"year": "", "jurisdiction": "household", "key": "grant_price:g1", "value": "30", "source": "web"}]
    )
    g = equity.grants(SCHED, params, TODAY).set_index("grant_id")
    assert g.loc["g1", ["grant_price", "price_from"]].tolist() == [30.0, "setting"]
    assert equity.grant_key("C-1234 A") == "c_1234_a"  # how a setting names a grant


def test_vesting_month_by_month_for_the_grants_lives_at_the_vests_value_then_the_latest_price():
    g = equity.grants(SCHED, None, TODAY)
    v = equity.vesting(SCHED, g, VESTED, PRICES, TODAY)
    assert list(v.columns) == equity.VEST_COLUMNS
    assert v["month"].min() == "2025-05" and v["month"].max() == "2027-07"  # the grants' whole lives
    aug = v[(v["month"] == "2026-08")].iloc[0]
    assert (aug["price"], aug["value"], aug["basis"]) == (45.0, 450.0, "vested")  # its vest's fair market value
    nov = v[(v["month"] == "2026-11")].iloc[0]
    assert (nov["price"], nov["value"], nov["basis"]) == (50.0, 500.0, "latest")
    option = v[(v["grant_id"] == "o1") & (v["month"] == "2027-07")].iloc[0]
    assert option["value"] == 100.0  # an option's 10 shares are worth the 10 a share the price is over its 40


def test_the_summary_values_the_shares_held_and_still_to_vest_at_the_latest_price():
    g = equity.grants(SCHED, None, TODAY)
    s = equity.summary(SCHED, g, LOTS, VESTED, PRICES).iloc[0]
    assert s[["symbol", "price", "price_date", "held", "basis", "value", "gain"]].tolist() == [
        "ACME",
        50.0,
        "2026-11-12",
        30.0,
        1200.0,
        1500.0,
        300.0,
    ]  # another symbol's lots are not the grants'
    assert s["unvested"] == 30.0 and s["unvested_value"] == 20 * 50 + 10 * 10  # the option's second tranche
    # a newer vest's fair market value is the price when no close is newer
    newer = pd.DataFrame([dict(VESTED.iloc[0], vest_date="2026-11-13", fmv="52")])
    assert equity.latest_price(PRICES, newer.assign(symbol="ACME"), "ACME") == (52.0, "2026-11-13")
    assert equity.latest_price(None, None, "ACME") == (None, "")


def test_no_grants_no_rows():
    empty = schedule()
    assert equity.grants(empty, None, TODAY).empty
    assert equity.vesting(empty, equity.grants(empty, None, TODAY), None, PRICES, TODAY).empty
    assert equity.summary(empty, equity.grants(empty, None, TODAY), LOTS, None, PRICES).empty
