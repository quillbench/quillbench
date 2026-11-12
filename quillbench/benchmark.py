"""
Benchmark creation: people write new items instead of answering existing ones.

A benchmark project is an ordinary project with ``task: create``. Its form lives
next to the manifest in ``benchmark_form.yaml``::

    connection: city_sql          # id of an admin-made connection (connections.py)
    participants: [alice, bob]
    threshold: 0.8                # question similarity that counts as a duplicate
    min_rows: 1                   # rows a query must return before it can be added
    slots:
      - {id: intro, type: text, label: About this task, text: "..."}
      - {id: schema, type: schema, label: Database schema}
      - {id: examples, type: examples, label: Examples, examples: [{question: ..., query: ...}]}
      - {id: question, type: question, label: Question, required: true}
      - {id: query, type: query, label: SQL query, required: true}
      - {id: result, type: result, label: Result, verdicts: [Correct, Incorrect, Uncertain]}
      - {id: difficulty, type: choice, label: Difficulty, options: [easy, medium, hard]}

Every entry gets a random id, unique across the project. Entries whose question is
``threshold`` or more similar to an accepted entry, from anyone, go to
``rejected.csv`` with the id and score of the entry they matched; the rest go to
``benchmark.csv``. Both files sit in the project folder under ``benchmark/``.

Each query is sorted into categories automatically (categories.py). The form can
give each category a number of points (``points``), a minimum each person should
reach in it (``targets``) and an overall minimum (``min_total``). Only entries whose
verdict is in the result slot's ``scored`` list earn points.
"""

from __future__ import annotations

import csv
import difflib
import fcntl
import json
import re
import secrets
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import categories
from .config import WorkspaceConfig

FORM_NAME = "benchmark_form.yaml"
ACCEPTED = "benchmark.csv"
REJECTED = "rejected.csv"

# type -> (what the admin sees in the "add a slot" menu, does the person fill it in)
SLOT_TYPES = {
    "text": ("Text block (instructions, domain notes)", False),
    "schema": ("Database schema (Schema tab settings)", False),
    "examples": ("Worked examples", False),
    "question": ("Question (the person writes it)", True),
    "query": ("Query editor with a Run button", True),
    "result": ("Query result (and optional verdict)", True),
    "choice": ("Label buttons (pick one)", True),
    "dropdown": ("Dropdown (pick one)", True),
    "multi": ("Tick boxes (pick any)", True),
    "rating": ("Slider", True),
    "short_text": ("Short answer", True),
    "long_text": ("Paragraph", True),
}
# Exactly one of each of these in every form.
CORE = ("question", "query", "result")
FIXED_COLUMNS = ["id", "author", "created_at", "question", "query", "row_count",
                 "result_columns", "result_preview", "categories", "verdict"]
DEFAULT_VERDICTS = ["Correct", "Incorrect", "Uncertain"]
REJECT_COLUMNS = ["similarity", "matched_id"]
PREVIEW_ROWS = 5


@dataclass
class Slot:
    id: str
    type: str
    label: str = ""
    help: str = ""
    required: bool = False
    text: str = ""                                   # text block / schema note
    options: list = field(default_factory=list)      # choice, dropdown, multi
    examples: list = field(default_factory=list)     # examples: [{question, query, note}]
    minimum: float = 0.0                             # rating
    maximum: float = 5.0
    step: float = 1.0
    confirm: str = "Is the result right for your question?"   # result: the question above the buttons
    tables: list = field(default_factory=list)       # schema: tables to show (empty = all)
    notes: dict = field(default_factory=dict)        # schema: table -> note written by the admin
    samples: bool = True                             # schema: offer "show example rows"
    ask_verdict: bool = False                        # result: ask people for a verdict at all
    verdicts: list = field(default_factory=lambda: list(DEFAULT_VERDICTS))   # result
    accept: list = field(default_factory=lambda: list(DEFAULT_VERDICTS))     # verdicts that may be added
    scored: list = field(default_factory=lambda: ["Correct"])                # verdicts that earn points

    @property
    def is_input(self) -> bool:
        return SLOT_TYPES[self.type][1]

    @property
    def column(self) -> bool:
        """Does this slot get its own column in the CSV (beyond the fixed ones)?"""
        return self.is_input and self.type not in CORE

    def to_dict(self) -> dict:
        out = {"id": self.id, "type": self.type, "label": self.label}
        if self.help:
            out["help"] = self.help
        if self.is_input and self.type not in CORE:
            out["required"] = self.required
        if self.type in ("text", "schema") and self.text:
            out["text"] = self.text
        if self.type == "schema":
            out.update(tables=list(self.tables), notes={k: v for k, v in self.notes.items() if str(v).strip()},
                       samples=self.samples)
        if self.type in ("choice", "dropdown", "multi"):
            out["options"] = list(self.options)
        if self.type == "examples":
            out["examples"] = list(self.examples)
        if self.type == "rating":
            out.update(minimum=self.minimum, maximum=self.maximum, step=self.step)
        if self.type == "result":
            out.update(ask_verdict=self.ask_verdict, confirm=self.confirm, verdicts=list(self.verdicts),
                       accept=list(self.accept), scored=list(self.scored))
        return out


@dataclass
class Form:
    connection: str = ""
    participants: list = field(default_factory=list)
    threshold: float = 0.8
    min_rows: int = 1
    slots: list = field(default_factory=list)
    points: dict = field(default_factory=dict)    # category id -> points (missing = catalogue default)
    targets: dict = field(default_factory=dict)   # category id -> minimum points per person
    min_total: int = 0                            # minimum total points per person

    def slot(self, kind: str) -> Slot | None:
        return next((s for s in self.slots if s.type == kind), None)

    def to_dict(self) -> dict:
        return {"connection": self.connection, "participants": list(self.participants),
                "threshold": self.threshold, "min_rows": self.min_rows,
                "points": dict(self.points), "targets": dict(self.targets),
                "min_total": self.min_total,
                "slots": [s.to_dict() for s in self.slots]}

    def score(self, cats: list[str]) -> tuple[int, str]:
        """An entry is worth its single most valuable category: (points, that category)."""
        if not cats:
            return 0, ""
        top = max(cats, key=self.points_for)    # ties: the first in catalogue order
        return self.points_for(top), top

    def points_for(self, cid: str) -> int:
        if cid in self.points:
            return int(self.points[cid])
        c = categories.BY_ID.get(cid)
        return c.points if c else 0


def default_slots(language: str = "sql") -> list[Slot]:
    word = "Cypher" if language == "cypher" else "SQL"
    return [
        Slot("intro", "text", "About this task",
             text="Read the schema and the examples, then write a question someone might ask "
                  f"about this data and the {word} query that answers it. Run the query and "
                  "check the result before adding it."),
        Slot("schema", "schema", "Database schema"),
        Slot("question", "question", "Question", required=True,
             help="Write it the way a person would ask it."),
        Slot("query", "query", f"{word} query", required=True),
        Slot("result", "result", "Result", required=True),
    ]


# ──────────────────────────────────────────────────────────────────────────
# Reading and writing the form
# ──────────────────────────────────────────────────────────────────────────
def is_benchmark(ws: WorkspaceConfig) -> bool:
    return bool(ws.projects) and ws.projects[0].task_spec.view == "creator"


def form_path(ws: WorkspaceConfig) -> Path:
    return ws.root / FORM_NAME


def _slot_from(raw: dict) -> Slot:
    kind = str(raw.get("type", ""))
    if kind not in SLOT_TYPES:
        raise ValueError(f"Unknown slot type '{kind}'.")
    known = {k: raw[k] for k in Slot.__dataclass_fields__ if k in raw}
    known["id"], known["type"] = str(raw.get("id") or kind), kind
    if kind in CORE:
        known["required"] = True
    if str(known.get("confirm", "")).startswith("I checked the result"):
        del known["confirm"]   # the text of the old tick box; the buttons ask a question instead
    return Slot(**known)


def load_form(ws: WorkspaceConfig) -> Form:
    path = form_path(ws)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
    raw = raw or {}
    return Form(connection=str(raw.get("connection", "")),
                participants=[str(p) for p in raw.get("participants", [])],
                threshold=float(raw.get("threshold", 0.8)),
                min_rows=int(raw.get("min_rows", 1)),
                slots=[_slot_from(s) for s in raw.get("slots", [])],
                points={str(k): int(v) for k, v in (raw.get("points") or {}).items()},
                targets={str(k): int(v) for k, v in (raw.get("targets") or {}).items() if int(v) > 0},
                min_total=int(raw.get("min_total", 0) or 0))


def check_form(form: Form) -> list[str]:
    """Problems that would stop people from using the form."""
    problems = []
    for kind in CORE:
        n = sum(s.type == kind for s in form.slots)
        if n != 1:
            problems.append(f"The form needs exactly one '{SLOT_TYPES[kind][0]}' slot (has {n}).")
    ids = [s.id for s in form.slots]
    if len(set(ids)) != len(ids):
        problems.append("Two slots have the same id.")
    clash = {s.id for s in form.slots if s.column} & set(FIXED_COLUMNS + REJECT_COLUMNS)
    if clash:
        problems.append(f"These slot ids are reserved: {sorted(clash)}.")
    for s in form.slots:
        if s.type in ("choice", "dropdown", "multi") and len(s.options) < 2:
            problems.append(f"Slot '{s.label or s.id}' needs at least two options.")
        if s.type == "result" and s.ask_verdict:
            if len(s.verdicts) < 2:
                problems.append("The result slot needs at least two verdict buttons.")
            elif not set(s.accept) & set(s.verdicts):
                problems.append("At least one verdict must allow adding the entry.")
        if s.type == "rating" and not s.minimum < s.maximum:
            problems.append(f"Slot '{s.label or s.id}': the lowest value must be below the highest.")
    if not 0.5 <= form.threshold <= 1.0:
        problems.append("The similarity limit must be between 50% and 100%.")
    if not form.connection:
        problems.append("Choose a database connection.")
    return problems


def save_form(ws: WorkspaceConfig, form: Form) -> None:
    path = form_path(ws)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(yaml.safe_dump(form.to_dict(), sort_keys=False, allow_unicode=True),
                   encoding="utf-8")
    tmp.replace(path)


def new_slot_id(form: Form, kind: str) -> str:
    taken = {s.id for s in form.slots} | set(FIXED_COLUMNS) | set(REJECT_COLUMNS)
    if kind in CORE and kind not in taken:
        return kind
    n = 1
    while f"{kind}_{n}" in taken:
        n += 1
    return f"{kind}_{n}"


def is_participant(ws: WorkspaceConfig, name: str) -> bool:
    return is_benchmark(ws) and name in load_form(ws).participants


# ──────────────────────────────────────────────────────────────────────────
# Similarity
# ──────────────────────────────────────────────────────────────────────────
def normalise(question: str) -> str:
    """Lower case, accents and punctuation dropped, spaces collapsed."""
    text = unicodedata.normalize("NFKD", str(question)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return text.strip()


def similarity(a: str, b: str) -> float:
    """0..1, how alike two questions read (difflib's ratio on the normalised text)."""
    a, b = normalise(a), normalise(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def most_similar(question: str, accepted: list[dict], threshold: float) -> tuple[float, str]:
    """The highest score against any accepted entry, and that entry's id."""
    target = normalise(question)
    best, best_id = 0.0, ""
    matcher = difflib.SequenceMatcher(None, autojunk=False)
    matcher.set_seq2(target)
    for row in accepted:
        other = normalise(row.get("question", ""))
        if not other:
            continue
        matcher.set_seq1(other)
        # Cheap upper bounds first: most pairs are nowhere near the limit.
        if matcher.real_quick_ratio() < max(best, threshold) or matcher.quick_ratio() < max(best, threshold):
            continue
        score = matcher.ratio()
        if score > best:
            best, best_id = score, str(row.get("id", ""))
    return best, best_id


def nearest(question: str, accepted: list[dict], n: int = 5) -> list[tuple[float, dict]]:
    """The ``n`` accepted entries whose questions read most like this one, best first."""
    scored = [(similarity(question, r.get("question", "")), r) for r in accepted]
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:n]


# ──────────────────────────────────────────────────────────────────────────
# The two CSV files
# ──────────────────────────────────────────────────────────────────────────
def data_dir(ws: WorkspaceConfig) -> Path:
    return ws.root / "benchmark"


def accepted_path(ws: WorkspaceConfig) -> Path:
    return data_dir(ws) / ACCEPTED


def rejected_path(ws: WorkspaceConfig) -> Path:
    return data_dir(ws) / REJECTED


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_accepted(ws: WorkspaceConfig) -> list[dict]:
    return _read_rows(accepted_path(ws))


def read_rejected(ws: WorkspaceConfig) -> list[dict]:
    return _read_rows(rejected_path(ws))


def _append(path: Path, row: dict) -> None:
    """Add one row; the header grows when the form gained a slot since the file began."""
    rows = _read_rows(path)
    header = list(rows[0].keys()) if rows else []
    new_cols = [c for c in row if c not in header]
    if not header or new_cols:
        header = header + new_cols
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header)
            w.writeheader()
            w.writerows(rows)
            w.writerow(row)
        tmp.replace(path)
        return
    with path.open("a", newline="", encoding="utf-8") as f:
        csv.DictWriter(f, fieldnames=header).writerow(row)


@contextmanager
def _locked(ws: WorkspaceConfig):
    """One writer at a time across every session of the server."""
    data_dir(ws).mkdir(parents=True, exist_ok=True)
    with (data_dir(ws) / ".lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _new_id(taken: set[str]) -> str:
    while True:
        candidate = "q_" + secrets.token_hex(4)
        if candidate not in taken:
            return candidate


@dataclass(frozen=True)
class Outcome:
    accepted: bool
    id: str
    similarity: float = 0.0
    matched_id: str = ""


def submit(ws: WorkspaceConfig, form: Form, author: str, question: str, query: str,
           columns: list, rows: list, answers: dict | None = None, verdict: str = "",
           language: str = "sql") -> Outcome:
    """Store one entry: in benchmark.csv, or in rejected.csv when it repeats a question.

    The caller has already run the query; ``columns`` and ``rows`` are its result.
    """
    question, query = question.strip(), query.strip()
    if not question or not query:
        raise ValueError("Both the question and the query are needed.")
    answers = answers or {}
    with _locked(ws):
        accepted, rejected = read_accepted(ws), read_rejected(ws)
        entry_id = _new_id({r.get("id", "") for r in accepted + rejected})
        row = {"id": entry_id, "author": author,
               "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "question": question, "query": query, "row_count": len(rows),
               "result_columns": json.dumps(list(columns), ensure_ascii=False, default=str),
               "result_preview": json.dumps(rows[:PREVIEW_ROWS], ensure_ascii=False, default=str),
               "categories": ";".join(categories.detect(query, language)), "verdict": verdict}
        for s in form.slots:
            if s.column:
                value = answers.get(s.id, "")
                row[s.id] = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
        score, matched = most_similar(question, accepted, form.threshold)
        if score >= form.threshold:
            row.update(similarity=f"{score:.3f}", matched_id=matched)
            _append(rejected_path(ws), row)
            return Outcome(False, entry_id, score, matched)
        # Kept for added entries too, so admins can see near misses.
        row.update(similarity=f"{score:.3f}", matched_id=matched if score else "")
        _append(accepted_path(ws), row)
        return Outcome(True, entry_id, score, matched)


def counts(ws: WorkspaceConfig) -> dict[str, dict[str, int]]:
    """``{person: {"added": n, "not_added": m}}`` for the progress page."""
    out: dict[str, dict[str, int]] = {}
    for r in read_accepted(ws):
        out.setdefault(r.get("author", ""), {"added": 0, "not_added": 0})["added"] += 1
    for r in read_rejected(ws):
        out.setdefault(r.get("author", ""), {"added": 0, "not_added": 0})["not_added"] += 1
    return out


# ──────────────────────────────────────────────────────────────────────────
# Points and profiles
# ──────────────────────────────────────────────────────────────────────────
def language_of(form: Form) -> str:
    from . import connections

    conn = connections.get(form.connection)
    return conn.language if conn else "sql"


def entry_categories(row: dict, language: str) -> list[str]:
    """Read again from the query, so every entry is judged by the same, current rules."""
    query = row.get("query", "")
    return categories.detect(query, language or categories.guess_language(query))


def _verdict(row: dict) -> str:
    # Entries from before verdict buttons were confirmed with a tick box: they count as correct.
    return row.get("verdict") or "Correct"


def earns_points(row: dict, form: Form) -> bool:
    result = form.slot("result")
    if not row.get("verdict") or not (result and result.ask_verdict):
        return True     # no verdict asked: every added entry counts
    return row["verdict"] in result.scored


@dataclass
class Profile:
    person: str
    added: int = 0
    not_added: int = 0
    total: int = 0
    per_category: dict = field(default_factory=dict)   # id -> {"entries": n, "points": p}
    verdicts: dict = field(default_factory=dict)       # verdict -> n
    levels: dict = field(default_factory=dict)         # complexity -> n
    entries: list = field(default_factory=list)        # accepted rows with "_categories", "_points", "_level"

    def missing(self, form: Form) -> dict[str, int]:
        """Points still needed, per category with a target."""
        return {c: need - self.per_category.get(c, {}).get("points", 0)
                for c, need in form.targets.items()
                if self.per_category.get(c, {}).get("points", 0) < need}

    def done(self, form: Form) -> bool:
        return self.total >= form.min_total and not self.missing(form)


def profiles(ws: WorkspaceConfig, form: Form | None = None) -> dict[str, Profile]:
    form = form or load_form(ws)
    language = language_of(form)
    out: dict[str, Profile] = {}
    for row in read_accepted(ws):
        p = out.setdefault(row.get("author", ""), Profile(row.get("author", "")))
        p.added += 1
        v = _verdict(row)
        p.verdicts[v] = p.verdicts.get(v, 0) + 1
        cats = entry_categories(row, language)
        worth, top = form.score(cats)
        level = categories.complexity(cats)
        earned = worth if earns_points(row, form) else 0
        # The total takes one score per entry (its best category). Per category we count
        # what the person has shown they can do, for the per-category minimums.
        for c in cats:
            slot = p.per_category.setdefault(c, {"entries": 0, "points": 0})
            slot["entries"] += 1
            slot["points"] += form.points_for(c) if earned else 0
        p.total += earned
        p.levels[level] = p.levels.get(level, 0) + 1
        p.entries.append({**row, "_categories": cats, "_points": earned, "_level": level, "_top": top})
    for row in read_rejected(ws):
        out.setdefault(row.get("author", ""), Profile(row.get("author", ""))).not_added += 1
    return out


def category_totals(ws: WorkspaceConfig, form: Form | None = None) -> dict[str, int]:
    """How many accepted entries fall in each category, across everyone."""
    form = form or load_form(ws)
    language = language_of(form)
    out: dict[str, int] = {}
    for row in read_accepted(ws):
        for c in entry_categories(row, language):
            out[c] = out.get(c, 0) + 1
    return out
