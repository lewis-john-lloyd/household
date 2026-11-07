"""Categorization: give every transaction a category and record what decided it.

Sources, first that applies wins:
  you, split     a split a person set on the line (txn_corrections field split):
                 categories and amounts that must come to the line's amount
  you            a category a person set on the line (txn_corrections), or the old
                 books' label on a legacy row, or a transfer pair
  order history  a store whose order history lists items (explain.order_services:
                 Amazon, Target and any the household adds): the line takes its category from what it bought,
                 split by item amount when the items differ, ahead of any payee rule
  rule           payee_rules: a pattern on the payee (and the bank's note, so a rule can
                 match USAA's own category: "\\| Cash$"), effective-dated; a rule marked
                 ask only pre-fills the guess and still asks for each line (Target,
                 paper checks)
  your decision  an answer given in review for a payee: it covers every line from
                 the same merchant (same_merchant), past and future
  waiting        a line an explanation download will name (Amazon, PayPal, Venmo,
                 Apple, Google): it keeps a provisional guess and is not asked about
                 until the export arrives (a line dated before the service's earliest
                 email or download is asked about instead: nothing will cover it); an
                 Amazon line inside the export's dates
                 that matched no order is asked about on its own, with the items
                 shipped around then as hints
  model          a scikit-learn classifier trained every run on the old books and on
                 every answer since, decided only when it is confident (THRESHOLD)
  bank           the bank's own category, mapped; used only as a guess

A split that does not add up, or names something that is not a category, puts
the line in review with the reason, like any open line.

Lines dated before REVIEW_FROM are never asked about; they take the best guess.
Everything else that nothing decided goes to the review tab, grouped by payee so
one answer settles a merchant. A transfer with no partner is asked about every
time, never settled by a payee answer; money out is guessed as the household's
unpaired_transfer_guess parameter (where such money usually goes), money in as a
transfer.
"""

import re
import warnings
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import explain, schema

REVIEW_FROM = "2025-07-01"
THRESHOLD = 0.9
ACCEPT = ("y", "yes", "ok")
UNPAIRED_PARAMETER = "unpaired_transfer_guess"
ONE_BY_ONE = tuple(explain.ORDER_SERVICES)  # an unmatched line from a store with order history is asked alone
DECISION_KIND = "category"

# processor prefixes in front of the merchant ("APLPAY WILLOW ST", "TST* CAFE", "SQ *SHOP")
PREFIX = re.compile(r"^(APLPAY|GGLPAY|GOOGLE PAY|TST\s?\*?|SQ\s?\*|SP\s|PY\s?\*|DD\s?\*|PAYPAL\s?\*|BT\s?\*|"
                    r"IC\s?\*|CKO\s?\*|FS\s?\*|PP\s?\*)\s*")
STATE = re.compile(r"(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|"
                   r"NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)")
NOISE = {"CO", "OP", "INC", "LLC", "COM", "WWW", "THE", "CORP", "LTD", "US", "USA"}
KEY_WORDS = 3
SPLIT_PART = re.compile(r"^(?P<category>[a-z][a-z0-9_]*)\s*[:=]?\s*(?:-?\$?-?(?P<amount>\d+(?:\.\d{1,2})?))?$")

# lines an explanation download names; until it arrives they wait
WAITING = (
    ("amazon", re.compile(r"AMAZON|AMZN|PRIME VIDEO|AUDIBLE|KINDLE")),
    ("paypal", re.compile(r"PAYPAL")),
    ("venmo", re.compile(r"VENMO")),
    ("apple", re.compile(r"APPLE\.COM|APPLE COM|ITUNES")),
    ("google", re.compile(r"GOOGLE\s?\*|GOOGLE PLAY")),
)

# the bank's category (USAA's CSV) to a household category, for guesses only
BANK_HINTS = {
    "groceries": "food", "fast food": "restaurant", "restaurants": "restaurant",
    "food & dining": "restaurant", "coffee shops": "restaurant", "alcohol & bars": "booze",
    "gas": "petrol", "pharmacy": "medical", "doctor": "medical", "dentist": "dental",
    "clothing": "clothing", "mortgage & rent": "mortgage", "mortgage payment": "mortgage",
    "life insurance": "insurance", "health insurance": "insurance", "interest income": "interest",
    "paycheck": "pay", "income": "pay", "bonus": "pay", "tuition": "tuition", "education": "tuition",
    "babysitter & daycare": "childcare", "kids activities": "camps", "mobile phone": "phone",
    "television": "media", "newspapers & magazines": "media", "books": "media", "music": "media",
    "pet food & supplies": "pets", "veterinary": "pets", "lawn & garden": "garden",
    "furnishings": "furnishing", "home improvement": "maintenance", "gifts & donations": "charity",
    "federal tax": "taxes", "state tax": "taxes", "hotel": "travel", "travel": "travel",
    "rental car & taxi": "travel", "movies & dvds": "culture", "amusement": "culture",
    "arts": "culture",
}


# --- payee keys ---------------------------------------------------------------------------------

def payee_key(text):
    """The merchant part of a payee, for grouping and for review answers.

    Processor prefixes, words with digits (store numbers, phone numbers, ids),
    punctuation, a trailing state and filler words go; the first KEY_WORDS words
    stay. "APLPAY WILLOW ST CO-OP SPRINGFIELD WI" and "WILLOW ST COOP #2" both
    start with WILLOW ST, which is what an answer for that key matches.
    """
    s = str(text).upper().strip()
    for _ in range(2):
        s = PREFIX.sub("", s)
    s = s.replace("*", " ").replace("'", "")
    words = _words(re.sub(r"\S*\d\S*", " ", s))
    if not words:  # every word had a digit in it ("BP#6602379EXPRESS"): keep the letters instead
        words = _words(re.sub(r"\d+", " ", s))
    return " ".join(words[:KEY_WORDS])


def _words(s):
    words = re.sub(r"[^A-Z& ]", " ", s).split()
    if len(words) > 2 and STATE.fullmatch(words[-1]):
        words = words[:-1]
    return [w for w in words if w not in NOISE]


PREFIX_CHARS = 5   # a key this long or longer covers every key that starts with it
SAME_CHARS = 10    # keys that agree on this many letters are the same merchant (cut-off names)


def _compact(key):
    return key.replace(" ", "")


def same_merchant(key, answered):
    """Whether an answer for `answered` covers a line whose key is `key`.

    Spaces don't count ("PAR MOKA" is "PARMOKA"). The answered key covers any key
    that starts with it (WILLOW ST covers WILLOW ST COOP), and two keys that
    agree on their first SAME_CHARS letters are one merchant whose name a card
    cut short ("LANTERN CAFEWESTSIDE" is "LANTERN CAFE").
    """
    a, b = _compact(key), _compact(answered)
    if not a or not b:
        return False
    if a == b:
        return True
    if len(b) >= PREFIX_CHARS and a.startswith(b):
        return True
    # the shared letters must reach past a first word, or every UNIVERSITY ... would be one merchant
    first = min(len(key.split()[0]), len(answered.split()[0]))
    return (len(a) >= SAME_CHARS and len(b) >= SAME_CHARS and a[:SAME_CHARS] == b[:SAME_CHARS]
            and first < SAME_CHARS)


def _longest_prefix(key, prefixes):
    """The answered key that covers this key, the most specific one first.

    prefixes: {first letters: [keys]} (see _by_first_word).
    """
    a = _compact(key)
    hits = [p for p in prefixes.get(a[:PREFIX_CHARS], []) if same_merchant(key, p)]
    if not hits:
        return None
    return max(hits, key=lambda p: (_compact(p) == a, a.startswith(_compact(p)), len(_compact(p))))


def _by_first_word(keys):
    out = {}
    for k in keys:
        if _compact(k):
            out.setdefault(_compact(k)[:PREFIX_CHARS], []).append(k)
    return out


# --- rules and decisions ------------------------------------------------------------------------

def _rules(payee_rules):
    if payee_rules is None or payee_rules.empty:
        return []
    out = []
    for r in payee_rules.to_dict("records"):
        pattern = str(r.get("pattern", "")).strip()
        if not pattern:
            continue
        prec = str(r.get("precedence", "")).strip()
        out.append({"pattern": pattern, "regex": re.compile(pattern, re.IGNORECASE),
                    "category": str(r.get("category", "")).strip(),
                    "effective": str(r.get("effective", "")).strip()[:10] or "0000",
                    "precedence": float(prec) if prec else 100.0,
                    "ask": str(r.get("ask", "")).strip().upper() == "TRUE"})
    return out


def _rule_text(payee, named, note):
    """What a payee rule's pattern is matched against: the payee, the name an explanation gave it, and
    the bank's note (USAA's ends with its own category, "... | Cash"), joined by " | "."""
    parts = [payee] + ([named] if named != payee else []) + ([note] if str(note).strip() else [])
    return " | ".join(parts)


def rule_for(payee, date, rules):
    """The payee rule in force on date for this payee, or None.

    Rows sharing a pattern are one rule over time: the latest effective on or
    before the date is in force (of two rows on one date, the later in the
    sheet), and a row with no category that does not ask stops the rule from
    its date (a rule that asks may leave the guess blank: paper checks). Among
    patterns that match, the lowest precedence wins.
    """
    live = {}
    for r in rules:
        if r["effective"] <= date and r["regex"].search(payee):
            if r["pattern"] not in live or r["effective"] >= live[r["pattern"]]["effective"]:
                live[r["pattern"]] = r
    live = {p: r for p, r in live.items() if r["category"] or r["ask"]}
    if not live:
        return None
    return min(live.values(), key=lambda r: (r["precedence"], r["pattern"]))


def payee_decisions(decisions):
    """{payee key: category} from review answers (proposal_decisions), latest answer per key."""
    if decisions is None or decisions.empty:
        return {}
    d = decisions[(decisions["kind"] == DECISION_KIND) & (decisions["decision"] == "approve")
                  & decisions["proposal_key"].astype(str).str.startswith("payee:")]
    d = d.sort_values("decided_at")
    return {str(k)[len("payee:"):]: str(v).strip() for k, v in zip(d["proposal_key"], d["note"])}


# --- model --------------------------------------------------------------------------------------

def _features(df):
    text = df["payee_text"] if "payee_text" in df else df["payee_raw"]
    words = text.astype(str).map(lambda s: " ".join(re.sub(r"[^A-Z& ]", " ", s.upper()).split()))
    amount = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    size = np.floor(np.log2(amount.abs() + 1)).astype(int).astype(str)
    sign = np.where(amount < 0, "OUT", "IN")
    return words + " AMT" + size + " " + sign


class Model:
    """Payee text (character and word n-grams) plus amount size and direction, logistic regression."""

    def __init__(self, labeled, weights):
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline, make_union

        self.classes = np.array(sorted(labeled["category"].unique()))
        self.pipe = None
        if len(self.classes) < 2:
            return
        self.pipe = make_pipeline(
            make_union(TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), sublinear_tf=True,
                                       min_df=2 if len(labeled) > 100 else 1),
                       TfidfVectorizer(token_pattern=r"\S+")),
            LogisticRegression(C=3, solver="saga", max_iter=200, tol=1e-3))
        with warnings.catch_warnings():  # saga stops at max_iter on some runs; the fit is still good
            warnings.simplefilter("ignore")
            self.pipe.fit(_features(labeled), labeled["category"],
                          logisticregression__sample_weight=np.asarray(weights, dtype=float))
        self.classes = self.pipe.classes_

    def proba(self, df):
        if df.empty:
            return np.zeros((0, len(self.classes)))
        if self.pipe is None:
            return np.zeros((len(df), len(self.classes)))
        return self.pipe.predict_proba(_features(df))


def training_set(df, exclude=()):
    """Labeled rows and their weights: the old books, heavier from 2023, and every answer since, heaviest."""
    labeled = df[(df["category"].astype(str).str.strip() != "")
                 & (df["decided_by"].isin(["old books", "you", "rule", "your decision"]))
                 & ~df["txn_id"].isin(set(exclude))
                 & (df["category"] != "transfer")]
    year = labeled["date"].astype(str).str[:4]
    weights = np.where(labeled["era"] != "legacy", 5.0, np.where(year >= "2023", 2.0, 1.0))
    return labeled, weights


# --- categorize ---------------------------------------------------------------------------------

def _bank_hint(note):
    parts = str(note).split("|")
    return BANK_HINTS.get(parts[-1].strip().lower(), "") if len(parts) > 1 else ""


def _covers(span, date):
    return bool(span) and span[0] <= date <= span[1]


def _waits(span, date):
    """Whether a line the service would explain should wait for it: when nothing from the service has come
    yet, or the line is newer than the latest of it. A line from before the earliest is not waited for: no
    later email or download will cover dates before the start, so it is asked about like any other."""
    return not span or date > span[1]


def _waiting(payee):
    upper = str(payee).upper()
    return next((service for service, pattern in WAITING if pattern.search(upper)), "")


def household_parameter(parameters, key, default=""):
    """A household setting from the parameters table (jurisdiction "household", no year)."""
    if parameters is None or parameters.empty:
        return default
    hit = parameters[(parameters["jurisdiction"] == "household") & (parameters["key"] == key)]
    return str(hit["value"].iloc[-1]).strip() if len(hit) else default


def parse_split(text, amount=None, known=None):
    """[(category, signed amount)] from a split such as "kids 20; hobby 15.50; gifts 40; family".

    Parts are separated by semicolons (or by commas when there is no semicolon);
    each is a category and an amount; a sign is ignored, since every part takes the
    line's own (a charge's parts are charges, a refund's are refunds). One part may
    leave its amount out and takes what the others leave of the line. With the
    line's amount given, the parts must come to it to the cent and take its sign;
    without it they are only read, and the part with no amount stays None.
    known, when given, is the set of categories a part may name. Raises
    ValueError saying what is wrong.
    """
    text = str(text).strip().lower()
    pieces = [p.strip() for p in (text.split(";") if ";" in text else text.split(","))]
    parts, rest = [], None
    for piece in filter(None, pieces):
        m = SPLIT_PART.match(piece.replace(",", "") if ";" in text else piece)
        if not m:
            raise ValueError(f"can't read {piece!r}: write a category and an amount")
        category, cents = m["category"], m["amount"]
        if known is not None and category not in known:
            raise ValueError(f"not a category: {category}")
        if category in [c for c, _ in parts] + [rest]:
            raise ValueError(f"{category} is named twice")
        if cents is None:
            if rest is not None:
                raise ValueError(f"only one part can leave its amount out ({rest} and {category} do)")
            rest = category
            parts.append((category, None))
            continue
        value = round(float(cents) * 100)
        if value <= 0:
            raise ValueError(f"nothing for {category}")
        parts.append((category, value))
    if not parts:
        raise ValueError("no parts")
    given = sum(v for _, v in parts if v is not None)
    if amount is None or str(amount).strip() in ("", "nan", "None"):  # checked against the line later
        return [(c, None if v is None else v / 100) for c, v in parts]
    line = round(abs(float(amount)) * 100)
    sign = -1 if float(amount) < 0 else 1
    if rest is not None:
        if given >= line:
            raise ValueError(f"the parts come to {given / 100:.2f}, leaving nothing of the line's "
                             f"{line / 100:.2f} for {rest}")
        parts = [(c, line - given if v is None else v) for c, v in parts]
    elif given != line:
        raise ValueError(f"the parts come to {given / 100:.2f}, the line is {line / 100:.2f}")
    return [(c, sign * v / 100) for c, v in parts]


def split_text(parts):
    """The canonical text of a split, amounts unsigned: "kids 20.00; family 11.87" (or "family" for a
    part still to take the rest)."""
    return "; ".join(c if v is None else f"{c} {abs(v):.2f}" for c, v in parts)


def categorize(transactions, payee_rules=None, decisions=None, unpaired=(), unpaired_guess="",
               explanations=None, review_from=REVIEW_FROM, categories=None):
    """Every transaction with category, decided_by, confidence, status and, when open, how it is asked.

    transactions are effective (corrections applied), transfer pairs marked and
    old category names aliased. unpaired: txn_ids of transfer lines with no
    partner; unpaired_guess: the category guessed for one that sends money out.
    explanations: what explain.explain matched (a payee named by PayPal, an
    Amazon split, each service's coverage). status is decided, open (asked in
    review) or waiting (for an explanation download); category is the decision
    or, failing one, the best guess, so totals are whole while the queue is
    open; items, for a line an order history explains, is JSON [[category, item,
    amount], ...] of what it bought; splits lists a split line's parts ("household -20.00; food -5.50"), from
    a person's split or an explanation. categories: the names a person's split
    may use (any, when not given).
    """
    df = transactions.copy().reset_index(drop=True)
    df["category"] = df["category"].astype(str).str.strip()
    df["split"] = df["split"].astype(str).str.strip() if "split" in df else ""
    known = None if categories is None else {str(c).strip() for c in categories}
    df["date"] = df["date"].astype(str).str[:10]
    explanations = explanations or {}
    named, split, covered = (explanations.get("payee", {}), explanations.get("split", {}),
                             explanations.get("coverage", {}))
    df["payee_text"] = [named.get(t, p) for t, p in zip(df["txn_id"], df["payee_raw"].astype(str))]
    hints = explanations.get("hints", {})
    df["hint"] = df["txn_id"].map(lambda t: hints.get(t, ""))
    contents = explanations.get("items", {})  # what an explained charge bought, as JSON, for the views
    df["items"] = df["txn_id"].map(lambda t: contents.get(t, ""))
    df["key"] = df["payee_text"].map(payee_key)
    unpaired = set(unpaired)
    rules, answered = _rules(payee_rules), payee_decisions(decisions)
    answered_index = _by_first_word(answered)

    out = []  # one dict per row: category, source, confidence, status, ask, guess, splits
    df["note"] = df["note"].astype(str) if "note" in df else ""
    for r in df[["txn_id", "date", "payee_raw", "payee_text", "note", "amount", "era", "category",
                 "key", "split"]].itertuples(index=False):
        if r.split and r.split != "nan":
            try:
                parts = parse_split(r.split, r.amount, known)
            except ValueError as e:  # ask about the line, saying why
                out.append({"decided_by": f"your split: {e}", "ask": "line"})
                continue
            biggest = max(parts, key=lambda cv: abs(cv[1]))[0]
            out.append({"category": biggest, "decided_by": "you, split", "status": "decided",
                        "splits": "; ".join(f"{c} {v:.2f}" for c, v in parts) if len(parts) > 1 else ""})
            continue
        if r.category:
            source = ("old books" if r.era == "legacy" else
                      "transfer pair" if r.category == "transfer" else "you")
            out.append({"category": r.category, "decided_by": source, "status": "decided"})
            continue
        payee = str(r.payee_raw)
        rule = rule_for(_rule_text(payee, r.payee_text, r.note), r.date, rules)
        service = _waiting(payee)
        if r.txn_id in split:  # what an order history says the line bought decides, ahead of any payee rule
            parts = split[r.txn_id]
            biggest = max(parts, key=lambda cv: abs(cv[1]))[0]
            out.append({"category": biggest, "decided_by": explain.order_service(payee, explanations.get("services")) or service
                        or "explained",
                        "status": "decided",
                        "splits": "; ".join(f"{c} {v:.2f}" for c, v in parts) if len(parts) > 1 else ""})
        elif rule and not rule["ask"]:
            out.append({"category": rule["category"], "decided_by": "rule", "status": "decided"})
        elif rule:  # guess from the rule, ask about this line
            out.append({"guess": rule["category"], "decided_by": "rule, asks each time", "ask": "line"})
        elif r.txn_id in unpaired:
            guess = unpaired_guess if float(r.amount) < 0 else "transfer"
            out.append({"guess": guess, "decided_by": "transfer with no partner", "ask": "line"})
        elif service in ONE_BY_ONE and _covers(covered.get(service), r.date) and r.txn_id not in named:
            # inside the export's dates but matched nothing: no merchant answer fits, ask about this line
            out.append({"decided_by": f"{service}, no matching order", "ask": "line"})
        elif (hit := _longest_prefix(r.key, answered_index)) is not None:
            out.append({"category": answered[hit], "decided_by": "your decision", "status": "decided"})
        elif service and r.txn_id not in named and _waits(covered.get(service), r.date):
            out.append({"decided_by": f"waiting for {service}", "status": "waiting", "ask": "wait"})
        elif not r.key:  # no letters to name a merchant by: ask about the line itself
            out.append({"ask": "line"})
        else:
            out.append({"ask": "payee"})
    columns = ["category", "decided_by", "confidence", "status", "ask", "guess", "splits"]
    state = pd.DataFrame(out, index=df.index).reindex(columns=columns)
    text = [c for c in columns if c != "confidence"]
    state[text] = state[text].astype(object).where(state[text].notna(), "").astype(str)
    state["confidence"] = pd.to_numeric(state["confidence"])
    df = df.drop(columns=["category"]).join(state)

    pending = df.index[df["ask"] != ""]
    if len(pending):
        _guess(df, pending, unpaired)
    early = (df["status"] == "open") & (df["date"] < review_from)
    df.loc[early, "decided_by"] = df.loc[early, "decided_by"] + ", before review"
    df.loc[early, "status"] = "decided"
    df.loc[df["status"] == "decided", "ask"] = ""
    return df


def _guess(df, pending, unpaired):
    """Fill guesses for pending rows (in place); a payee group the model is sure of is decided."""
    labeled_rows, weights = training_set(df, exclude=unpaired)
    model = Model(labeled_rows, weights)
    rows = df.loc[pending]
    probs = pd.DataFrame(model.proba(rows), index=rows.index, columns=model.classes)
    # one guess per payee group (the mean over its lines), one per line otherwise
    group = np.where(rows["ask"] == "payee", "k:" + rows["key"], "t:" + rows["txn_id"].astype(str))
    if probs.shape[1]:
        mean = probs.groupby(group).transform("mean")
        best, conf = mean.idxmax(axis=1), mean.max(axis=1).round(3)
    else:  # nothing labeled yet to learn from
        best, conf = pd.Series("", index=rows.index), pd.Series(0.0, index=rows.index)
    hint = rows["note"].map(_bank_hint)
    use_hint = (conf < 0.5) & (hint != "")
    guess = best.where(~use_hint, hint)
    kind = rows["ask"]

    sure = (kind == "payee") & (conf >= THRESHOLD) & ~use_hint
    idx = sure.index[sure]
    df.loc[idx, "category"] = guess[sure]
    df.loc[idx, ["decided_by", "status"]] = ["model", "decided"]

    ask = (kind == "payee") & ~sure
    idx = ask.index[ask]
    df.loc[idx, "guess"] = guess[ask]
    df.loc[idx, "decided_by"] = np.where(use_hint[ask], "bank", "model")
    df.loc[idx, "status"] = "open"

    line = kind == "line"
    idx = line.index[line]
    blank = df.loc[idx, "guess"] == ""
    df.loc[blank.index[blank], "guess"] = guess[line][blank]
    df.loc[idx, "status"] = "open"

    wait = kind == "wait"
    df.loc[wait.index[wait], "guess"] = guess[wait]

    df.loc[pending, "confidence"] = conf
    df.loc[pending, "category"] = df.loc[pending, "category"].where(df.loc[pending, "category"] != "",
                                                                    df.loc[pending, "guess"])


# --- review ---------------------------------------------------------------------------------------

REVIEW_COLUMNS = schema.table(schema.LEDGER, "review").columns


def _merge_keys(keys):
    """Each key to the key its merchant is asked under: keys that are one merchant (same_merchant
    either way round) form a group, named by its shortest key."""
    ordered = sorted(set(keys), key=lambda k: (len(_compact(k)), k))
    parent = {k: k for k in ordered}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    index = _by_first_word(ordered)
    for k in ordered:
        for other in index.get(_compact(k)[:PREFIX_CHARS], []):
            if other != k and (same_merchant(k, other) or same_merchant(other, k)):
                a, b = find(k), find(other)
                if a != b:
                    first, second = sorted((a, b), key=lambda x: (len(_compact(x)), x))
                    parent[second] = first
    return {k: find(k) for k in ordered}


def queue(categorized, carry=None):
    """The review tab: one row per open payee group, then one row per line asked about singly.

    carry: rows of the previous review tab keyed by key; an answer that could not
    be used, or a note, is kept on the row with the same key.
    """
    open_ = categorized[categorized["status"] == "open"].copy()
    rows = []
    payees = open_[open_["ask"] == "payee"].copy()
    if not payees.empty:
        root = _merge_keys(payees["key"])
        payees["group"] = payees["key"].map(root)
        for key, g in payees.groupby("group"):
            amounts = pd.to_numeric(g["amount"])
            guess = g["guess"].mode().iloc[0]
            rows.append({"key": key, "answer": "", "split": "", "guess": guess, "lines": len(g),
                         "total": round(float(amounts.sum()), 2),
                         "example": " | ".join(g["payee_raw"].value_counts().index[:3]), "first": g["date"].min(),
                         "last": g["date"].max(), "accounts": ", ".join(sorted(set(g["account_id"]))),
                         "guessed_by": g["decided_by"].mode().iloc[0],
                         "confidence": round(float(pd.to_numeric(g["confidence"]).mean()), 3),
                         "kind": "payee", "note": ""})
    rows.sort(key=lambda r: (-r["lines"], -abs(r["total"]), r["key"]))
    singles = open_[open_["ask"] == "line"].sort_values(["date", "txn_id"])
    for r in singles.to_dict("records"):
        rows.append({"key": r["txn_id"], "answer": "", "split": "", "guess": r["guess"], "lines": 1,
                     "total": round(float(r["amount"]), 2),
                     "example": r["payee_raw"] + (f" | {r['hint']}" if r.get("hint") else ""), "first": r["date"],
                     "last": r["date"], "accounts": r["account_id"], "guessed_by": r["decided_by"],
                     "confidence": r["confidence"], "kind": "line", "note": ""})
    out = pd.DataFrame(rows, columns=list(REVIEW_COLUMNS))
    if carry is not None and not carry.empty:
        kept = carry.set_index("key")
        for i, key in out["key"].items():
            if key in kept.index:
                out.at[i, "answer"] = kept.at[key, "answer"]
                out.at[i, "split"] = kept.at[key, "split"] if "split" in kept else ""
                out.at[i, "note"] = kept.at[key, "note"]
        # a row typed in by hand (a split for a decided line) stays, with its note, until it is fixed
        typed = carry[~carry["key"].isin(set(out["key"])) & (carry.get("kind", "") == "line")]
        typed = typed.reindex(columns=list(REVIEW_COLUMNS))
        if not typed.empty:
            out = pd.concat([out, typed.astype(object).where(typed.notna(), "")], ignore_index=True)
    return out


def harvest(review, categories, now=None):
    """Answers in the review tab to ledger rows.

    Returns (decisions, corrections, left): a payee answer becomes a
    proposal_decisions row for "payee:<key>" (the key as it stands in the tab,
    so editing it widens or narrows what the answer covers); a line answer
    becomes a txn_corrections row. "y" takes the guess. A line's split column
    ("kids 20; hobby 15.50; family", parse_split; a split typed in answer counts
    too) is checked against the row's total when it has one and becomes a split
    correction; a split, when given, is the line's answer. A row typed in by
    hand with a txn_id as key and kind line answers that line, so any line can be
    split, decided or not. Rows whose answer is not a category or a split that
    adds up stay, with the reason in note.
    """
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    known = {str(c).strip() for c in categories}
    decisions, corrections, left = [], [], []
    for r in review.to_dict("records"):
        answer = str(r.get("answer", "")).strip()
        typed_split = str(r.get("split", "") or "").strip()
        typed_split = "" if typed_split == "nan" else typed_split
        if typed_split:
            answer = typed_split
        if not answer:
            continue
        category = str(r.get("guess", "")).strip() if answer.lower() in ACCEPT else answer.lower()
        key = str(r.get("key", "")).strip()
        if key and (typed_split or (category not in known
                                    and (";" in answer or "," in answer or re.search(r"\d", answer)))):
            if r.get("kind") != "line":
                left.append({**r, "note": "a split answers one line: answer a merchant with one category"})
                continue
            try:
                parts = parse_split(answer, r.get("total"), known)
            except ValueError as e:
                left.append({**r, "note": f"split: {e}"})
                continue
            corrections.append({"txn_id": key, "field": "split", "value": split_text(parts), "recorded_at": now,
                                "reason": "review", "source": "hand"})
            continue
        if category not in known or not key:
            left.append({**r, "note": f"not a category: {answer}" if key else "no key"})
            continue
        if r.get("kind") == "line":
            corrections.append({"txn_id": key, "field": "category", "value": category, "recorded_at": now,
                                "reason": "review", "source": "hand"})
        else:
            decisions.append({"proposal_key": f"payee:{payee_key(key) or key.upper()}", "kind": DECISION_KIND,
                              "decided_at": now, "decision": "approve", "note": category})
    return (pd.DataFrame(decisions, columns=["proposal_key", "kind", "decided_at", "decision", "note"]),
            pd.DataFrame(corrections, columns=["txn_id", "field", "value", "recorded_at", "reason", "source"]),
            pd.DataFrame(left, columns=list(REVIEW_COLUMNS)))


def summary(categorized, review_from=REVIEW_FROM):
    """Counts for the run log: how lines from review_from on were decided, what is open, what waits."""
    recent = categorized[(categorized["era"] != "legacy") & (categorized["date"] >= review_from)]
    decided = recent[recent["status"] == "decided"]["decided_by"].value_counts().to_dict()
    waiting = recent[recent["status"] == "waiting"]["decided_by"].value_counts().to_dict()
    open_ = recent[recent["status"] == "open"]
    return {"decided": decided, "waiting": waiting, "open_lines": len(open_)}


def actual_rows(categorized):
    """One row per category per transaction: a split line becomes one row per part."""
    whole = categorized[categorized["splits"] == ""]
    parts = []
    for r in categorized[categorized["splits"] != ""].to_dict("records"):
        for piece in r["splits"].split("; "):
            category, amount = piece.rsplit(" ", 1)
            parts.append({**r, "category": category, "amount": float(amount)})
    if not parts:
        return whole
    return pd.concat([whole, pd.DataFrame(parts)], ignore_index=True)


def rename(categorized, aliases):
    """Apply category_aliases to what categorization produced (rules and answers given under an old
    name, such as a category since renamed)."""
    if aliases is None or aliases.empty:
        return categorized
    names = dict(zip(aliases["alias"].astype(str).str.strip(), aliases["category"].astype(str).str.strip()))
    out = categorized.copy()
    for column in ("category", "guess"):
        out[column] = out[column].map(lambda c: names.get(c, c))
    return out
