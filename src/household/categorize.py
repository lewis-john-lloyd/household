"""Categorization: give every transaction a category and record what decided it.

Sources, first that applies wins:
  you            a category a person set on the line (txn_corrections), or the old
                 books' label on a legacy row, or a transfer pair
  rule           payee_rules: a pattern on the payee, effective-dated; a rule marked
                 ask only pre-fills the guess and still asks for each line (Target,
                 paper checks)
  your decision  an answer given in review for a payee: it covers every line whose
                 payee key starts with the answered key, past and future
  waiting        a line an explanation download will name (Amazon, PayPal, Venmo,
                 Apple, Google): it keeps a provisional guess and is not asked about
                 until the export arrives
  model          a scikit-learn classifier trained every run on the old books and on
                 every answer since, decided only when it is confident (THRESHOLD)
  bank           the bank's own category, mapped; used only as a guess

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

from . import schema

REVIEW_FROM = "2025-07-01"
THRESHOLD = 0.9
ACCEPT = ("y", "yes", "ok")
UNPAIRED_PARAMETER = "unpaired_transfer_guess"
DECISION_KIND = "category"

# processor prefixes in front of the merchant ("APLPAY WILLOW ST", "TST* CAFE", "SQ *SHOP")
PREFIX = re.compile(r"^(APLPAY|GGLPAY|GOOGLE PAY|TST\s?\*?|SQ\s?\*|SP\s|PY\s?\*|DD\s?\*|PAYPAL\s?\*|BT\s?\*|"
                    r"IC\s?\*|CKO\s?\*|FS\s?\*|PP\s?\*)\s*")
STATE = re.compile(r"(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|"
                   r"NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)")
NOISE = {"CO", "OP", "INC", "LLC", "COM", "WWW", "THE", "CORP", "LTD", "US", "USA"}
KEY_WORDS = 3

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
    "television": "subscriptions", "newspapers & magazines": "subscriptions",
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
    s = re.sub(r"\S*\d\S*", " ", s)
    s = re.sub(r"[^A-Z& ]", " ", s)
    words = s.split()
    if len(words) > 2 and STATE.fullmatch(words[-1]):
        words = words[:-1]
    words = [w for w in words if w not in NOISE]
    return " ".join(words[:KEY_WORDS])


def _starts_with(key, prefix):
    k, p = key.split(), prefix.split()
    return bool(p) and k[:len(p)] == p


def _longest_prefix(key, prefixes):
    """prefixes: {first word: [keys]} (see _by_first_word)."""
    words = key.split()
    hits = [p for p in prefixes.get(words[0], []) if _starts_with(key, p)] if words else []
    return max(hits, key=lambda p: len(p.split())) if hits else None


def _by_first_word(keys):
    out = {}
    for k in keys:
        if k.split():
            out.setdefault(k.split()[0], []).append(k)
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


def rule_for(payee, date, rules):
    """The payee rule in force on date for this payee, or None.

    Rows sharing a pattern are one rule over time: the latest effective on or
    before the date is in force. Among patterns that match, the lowest
    precedence wins.
    """
    live = {}
    for r in rules:
        if r["effective"] <= date and r["regex"].search(payee):
            if r["pattern"] not in live or r["effective"] > live[r["pattern"]]["effective"]:
                live[r["pattern"]] = r
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
    words = df["payee_raw"].astype(str).map(lambda s: " ".join(re.sub(r"[^A-Z& ]", " ", s.upper()).split()))
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


def _waiting(payee):
    upper = str(payee).upper()
    return next((service for service, pattern in WAITING if pattern.search(upper)), "")


def household_parameter(parameters, key, default=""):
    """A household setting from the parameters table (jurisdiction "household", no year)."""
    if parameters is None or parameters.empty:
        return default
    hit = parameters[(parameters["jurisdiction"] == "household") & (parameters["key"] == key)]
    return str(hit["value"].iloc[-1]).strip() if len(hit) else default


def categorize(transactions, payee_rules=None, decisions=None, unpaired=(), unpaired_guess="",
               review_from=REVIEW_FROM):
    """Every transaction with category, decided_by, confidence, status and, when open, how it is asked.

    transactions are effective (corrections applied), transfer pairs marked and
    old category names aliased. unpaired: txn_ids of transfer lines with no
    partner; unpaired_guess: the category guessed for one that sends money out.
    status is decided, open (asked in review) or waiting (for an
    explanation download); category is the decision or, failing one, the best
    guess, so totals are whole while the queue is open.
    """
    df = transactions.copy().reset_index(drop=True)
    df["category"] = df["category"].astype(str).str.strip()
    df["key"] = df["payee_raw"].map(payee_key)
    df["date"] = df["date"].astype(str).str[:10]
    unpaired = set(unpaired)
    rules, answered = _rules(payee_rules), payee_decisions(decisions)
    answered_index = _by_first_word(answered)

    out = []  # one dict per row: category, source, confidence, status, ask, guess
    for r in df[["txn_id", "date", "payee_raw", "amount", "era", "category", "key"]].itertuples(index=False):
        if r.category:
            source = ("old books" if r.era == "legacy" else
                      "transfer pair" if r.category == "transfer" else "you")
            out.append({"category": r.category, "decided_by": source, "status": "decided"})
            continue
        payee = str(r.payee_raw)
        rule = rule_for(payee, r.date, rules)
        if rule and not rule["ask"]:
            out.append({"category": rule["category"], "decided_by": "rule", "status": "decided"})
        elif rule:  # guess from the rule, ask about this line
            out.append({"guess": rule["category"], "decided_by": "rule, asks each time", "ask": "line"})
        elif r.txn_id in unpaired:
            guess = unpaired_guess if float(r.amount) < 0 else "transfer"
            out.append({"guess": guess, "decided_by": "transfer with no partner", "ask": "line"})
        elif (hit := _longest_prefix(r.key, answered_index)) is not None:
            out.append({"category": answered[hit], "decided_by": "your decision", "status": "decided"})
        elif service := _waiting(payee):
            out.append({"decided_by": f"waiting for {service}", "status": "waiting", "ask": "wait"})
        else:
            out.append({"ask": "payee"})
    state = pd.DataFrame(out, index=df.index).reindex(
        columns=["category", "decided_by", "confidence", "status", "ask", "guess"])
    text = ["category", "decided_by", "status", "ask", "guess"]
    state[text] = state[text].fillna("").astype(str)
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
    """Each key to the shortest key among them that it starts with (WILLOW ST COOP -> WILLOW ST)."""
    ordered = sorted(set(keys), key=lambda k: (len(k.split()), k))
    root = {}
    for k in ordered:
        root[k] = next((r for r in ordered if len(r.split()) < len(k.split()) and _starts_with(k, r)
                        and root.get(r) == r), k)
    return root


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
            rows.append({"key": key, "answer": "", "guess": guess, "lines": len(g),
                         "total": round(float(amounts.sum()), 2),
                         "example": g["payee_raw"].mode().iloc[0], "first": g["date"].min(),
                         "last": g["date"].max(), "accounts": ", ".join(sorted(set(g["account_id"]))),
                         "guessed_by": g["decided_by"].mode().iloc[0],
                         "confidence": round(float(pd.to_numeric(g["confidence"]).mean()), 3),
                         "kind": "payee", "note": ""})
    rows.sort(key=lambda r: (-r["lines"], -abs(r["total"]), r["key"]))
    singles = open_[open_["ask"] == "line"].sort_values(["date", "txn_id"])
    for r in singles.to_dict("records"):
        rows.append({"key": r["txn_id"], "answer": "", "guess": r["guess"], "lines": 1,
                     "total": round(float(r["amount"]), 2), "example": r["payee_raw"], "first": r["date"],
                     "last": r["date"], "accounts": r["account_id"], "guessed_by": r["decided_by"],
                     "confidence": r["confidence"], "kind": "line", "note": ""})
    out = pd.DataFrame(rows, columns=list(REVIEW_COLUMNS))
    if carry is not None and not carry.empty:
        kept = carry.set_index("key")
        for i, key in out["key"].items():
            if key in kept.index:
                out.at[i, "answer"] = kept.at[key, "answer"]
                out.at[i, "note"] = kept.at[key, "note"]
    return out


def harvest(review, categories, now=None):
    """Answers in the review tab to ledger rows.

    Returns (decisions, corrections, left): a payee answer becomes a
    proposal_decisions row for "payee:<key>" (the key as it stands in the tab,
    so editing it widens or narrows what the answer covers); a line answer
    becomes a txn_corrections row. "y" takes the guess. Rows whose answer is not
    a category stay, with the reason in note.
    """
    now = now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    known = {str(c).strip() for c in categories}
    decisions, corrections, left = [], [], []
    for r in review.to_dict("records"):
        answer = str(r.get("answer", "")).strip()
        if not answer:
            continue
        category = str(r.get("guess", "")).strip() if answer.lower() in ACCEPT else answer.lower()
        key = str(r.get("key", "")).strip()
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
