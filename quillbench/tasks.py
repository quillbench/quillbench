"""
Task types.

A task type says what the annotator records for an item and how the item is laid
out. Each one is a ``TaskSpec``; the annotation screen, the progress counts, the
quality statistics and the exports all read the spec instead of branching on the
task name. Adding a task type means adding a spec here (and, only if it needs a new
layout, a view in ``quillbench/ui``).

Two families:

* **Query-review tasks** (``verdict`` ... ``pair_semantics``): the eight task types
  with stable labels so existing progress files read
  exactly as before. Their label sets and the reasons behind them are documented
  where they were designed -- see the comments below, carried over unchanged.
* **Generic tasks** (``choice``, ``rating``, ``text``): declared entirely in a
  workspace manifest -- the labels, the scale, and which item fields to show -- so
  a new paper needs no code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LabelSpec:
    value: str          # what is written to the progress file
    text: str           # button text
    help: str = ""      # tooltip: the operative definition at the moment of deciding
    primary: bool = False
    glyph: str = "⚪"    # navigator marker

    @property
    def tone(self) -> str:
        """Button colour family, from the label's meaning: good, bad, warn or neu."""
        return {"🟢": "good", "🔴": "bad", "🟠": "warn", "🟡": "warn"}.get(self.glyph, "neu")


@dataclass(frozen=True)
class RatingSpec:
    """A numeric scale. ``unsure_label`` (if set) is offered as a separate button."""

    minimum: float = 0.0
    maximum: float = 1.0
    step: float = 0.05
    default: float | None = None
    left_label: str = ""
    right_label: str = ""
    unsure_label: str = ""
    level: str = "interval"   # for agreement statistics: interval or ordinal
    # Words placed along the scale, e.g. ((0, "false"), (0.5, "half true"), (1, "true")).
    marks: tuple = ()

    def word_for(self, value: float) -> str:
        """The mark nearest to ``value``, for the large read-out above the slider."""
        if not self.marks:
            return ""
        return min(self.marks, key=lambda m: abs(m[0] - value))[1]


@dataclass(frozen=True)
class TaskSpec:
    name: str
    kind: str                         # "labels", "rating" or "text"
    view: str                         # "query_review", "pair", "adjudication" or "generic"
    labels: tuple[LabelSpec, ...] = ()
    reason_required: tuple[str, ...] = ()
    note_label: str = "Note (optional)"
    note_placeholder: str = ""
    results_withheld: bool = False    # task never shows execution results; gold cache optional
    shows_query: bool = True
    rating: RatingSpec | None = None
    agreement_level: str = "nominal"
    # Labels from an earlier version of a project's task that still count as answers
    # (e.g. a round first recorded with Correct/Incorrect, later switched to Accept/Drop).
    also_accept: tuple[str, ...] = ()

    @property
    def values(self) -> tuple[str, ...]:
        return tuple(lab.value for lab in self.labels)

    def is_labeled(self, value) -> bool:
        """Has the annotator recorded an answer? (Skips and blanks are not answers.)"""
        text = "" if value is None else str(value).strip()
        if not text or text.lower() == "nan":
            return False
        if self.kind == "labels":
            return text in self.values or text in self.also_accept
        if self.kind == "rating":
            if self.rating and self.rating.unsure_label and text == "UNSURE":
                return True
            try:
                num = float(text)
            except ValueError:
                return False
            return math.isfinite(num)
        return True  # text: any non-empty answer

    def glyph_for(self, value) -> str:
        text = "" if value is None else str(value).strip()
        for lab in self.labels:
            if lab.value == text:
                return lab.glyph
        if text and text in self.also_accept:
            return "🔵"
        if self.kind == "rating" and self.is_labeled(text):
            return "🔵" if text != "UNSURE" else "⚫"
        if self.kind == "text" and text:
            return "🔵"
        return "⚪"


def _L(value, text, help="", primary=False, glyph="⚪"):
    return LabelSpec(value, text, help, primary, glyph)


# ──────────────────────────────────────────────────────────────────────────
# Query-review tasks
# ──────────────────────────────────────────────────────────────────────────
VERDICT = TaskSpec(
    "verdict", "labels", "query_review",
    labels=(_L("CORRECT", "Correct", primary=True, glyph="🟢"),
            _L("INCORRECT", "Incorrect", glyph="🔴"),
            _L("UNCERTAIN", "Uncertain", glyph="🟡")),
    reason_required=("INCORRECT", "UNCERTAIN"),
    note_label="Note (required for Incorrect or Uncertain)",
    note_placeholder=("One sentence: what is wrong with the question or the query, or what you "
                      "are unsure about. Optional if you mark the item Correct."),
)

# The adjudication task asks a different question of a different object: whether a
# PREDICTED query answers the natural-language question, rather than whether a reference
# result does. INSTANCE_ONLY is the label that earns the study its keep -- a prediction
# that is right on this database and would be wrong on another one is exactly what result
# comparison cannot see.
ADJUDICATION = TaskSpec(
    "adjudication", "labels", "adjudication",
    labels=(
        _L("EQUIVALENT", "Equivalent",
           "Same meaning as the reference query. Would agree on ANY database with this schema.",
           glyph="🟢"),
        _L("INSTANCE_ONLY", "Correct only here",
           "Answers the question on this database, but would diverge on another valid one "
           "— e.g. a missing filter that happens not to matter for this data.", glyph="🟠"),
        _L("WRONG", "Wrong",
           "Does not answer the question asked, whatever the results may have been.", glyph="🔴"),
        _L("AMBIGUOUS", "Ambiguous",
           "The question genuinely admits both readings; the prediction takes the other one.",
           glyph="🟡"),
        _L("CANT_TELL", "Can't tell", "Not decidable with the information shown.", glyph="⚫"),
    ),
    reason_required=("INSTANCE_ONLY", "AMBIGUOUS"),
    note_label="Note (required for 'Correct only here' and 'Ambiguous')",
    note_placeholder="Say what makes the difference — which condition is missing, which reading you took…",
    results_withheld=True,
)

# Gate review: the item failed an automatic quality gate -- what should happen to it.
# GATE_WRONG is the label that earns the task its keep: the gates are new and the
# annotators are the ones positioned to find where they misfire.
GATE_REVIEW = TaskSpec(
    "gate_review", "labels", "query_review",
    labels=(_L("REWRITE_QUESTION", "Rewrite question", primary=True, glyph="🟠"),
            _L("REWRITE_QUERY", "Rewrite query", glyph="🟠"),
            _L("DROP", "Drop item", glyph="🔴"),
            _L("GATE_WRONG", "Gate is wrong", glyph="🟢"),
            _L("CANT_TELL", "Can't tell", glyph="⚫")),
    reason_required=("GATE_WRONG",),
    note_label="Annotator note (optional)",
    note_placeholder="Observations, reservations, or reasoning supporting your judgment…",
)

# The first gate-review round agreed with itself only about half the time; two causes
# were in the task: overlapping repair labels, and collisions shown without the partner.
# This set asks one question with one answer; what to change goes in the note.
GATE_CALIBRATION = TaskSpec(
    "gate_calibration", "labels", "query_review",
    labels=(_L("DEFECT", "Yes — something is wrong", primary=True, glyph="🔴"),
            _L("NO_DEFECT", "No — the item is fine", glyph="🟢"),
            _L("CANT_TELL", "Can't tell", glyph="⚫")),
    note_label="What would you change? (optional)",
    note_placeholder=("If you said something is wrong: reword the question, fix the query, or "
                      "drop it — and why. This does not affect your verdict above."),
)

# New-item review in two roles. The question reviewer never sees the query, so a
# faithful implementation of a bad question cannot make the question look good.
ITEM_QUESTION = TaskSpec(
    "item_question", "labels", "query_review",
    labels=(_L("QUESTION_OK", "Question is fine", primary=True, glyph="🟢"),
            _L("QUESTION_PROBLEM", "Question has a problem", glyph="🔴"),
            _L("CANT_TELL", "Can't tell", glyph="⚫")),
    reason_required=("QUESTION_PROBLEM",),
    note_label="What is wrong with the question? (required if you said there is a problem)",
    note_placeholder=("One sentence. E.g. \"'highest' does not say highest total or highest "
                      "average\"."),
    shows_query=False,
)
ITEM_QUERY = TaskSpec(
    "item_query", "labels", "query_review",
    labels=(_L("QUERY_OK", "Query is correct", primary=True, glyph="🟢"),
            _L("QUERY_PROBLEM", "Query has a problem", glyph="🔴"),
            _L("CANT_TELL", "Can't tell", glyph="⚫")),
    reason_required=("QUERY_PROBLEM",),
    note_label="What is wrong with the query? (required if you said there is a problem)",
    note_placeholder="One sentence: which filter, grouping or rule the query gets wrong.",
)

# Disposition of an item already read in both roles: a decision, not another reading.
# The repair labels are the gate round's on purpose, so outcomes compare directly.
ITEM_DISPOSITION = TaskSpec(
    "item_disposition", "labels", "query_review",
    labels=(_L("ACCEPT", "Accept as is", primary=True, glyph="🟢"),
            _L("REWRITE_QUESTION", "Rewrite question", glyph="🟠"),
            _L("REWRITE_QUERY", "Rewrite query", glyph="🟠"),
            _L("DROP", "Drop item", glyph="🔴"),
            _L("CANT_TELL", "Can't tell", glyph="⚫")),
    reason_required=("REWRITE_QUESTION", "REWRITE_QUERY", "DROP", "CANT_TELL"),
    note_label="Why? (required unless you accepted the item)",
    note_placeholder="One sentence, and say what the fix is.",
)

# Blinded A/B semantics: the labeler is not told which query is the model's. No button
# is primary, because making one the default-styled choice is itself a nudge.
PAIR_SEMANTICS = TaskSpec(
    "pair_semantics", "labels", "pair",
    labels=(_L("BOTH", "Both answer it", glyph="🟢"),
            _L("ONLY_A", "Only A", glyph="🟠"),
            _L("ONLY_B", "Only B", glyph="🟠"),
            _L("NEITHER", "Neither", glyph="🔴"),
            _L("UNCLEAR", "Unclear", glyph="🟡")),
    note_label="Note (optional)",
    note_placeholder=("Anything worth recording — why the question is ambiguous, what the two "
                      "queries actually differ on, which reading you took. Read only after scoring."),
    results_withheld=True,
)

# ──────────────────────────────────────────────────────────────────────────
# Generic tasks (labels and scales come from the manifest)
# ──────────────────────────────────────────────────────────────────────────
CHOICE = TaskSpec("choice", "labels", "generic", note_label="Note (optional)")
RATING = TaskSpec("rating", "rating", "generic", rating=RatingSpec(),
                  note_label="Note (optional)", agreement_level="interval")
TEXT = TaskSpec("text", "text", "generic", note_label="Note (optional)")

BUILTIN: dict[str, TaskSpec] = {
    t.name: t for t in (VERDICT, ADJUDICATION, GATE_REVIEW, GATE_CALIBRATION, ITEM_QUESTION,
                        ITEM_QUERY, ITEM_DISPOSITION, PAIR_SEMANTICS, CHOICE, RATING, TEXT)
}
TASKS = tuple(BUILTIN)
QUERY_TASKS = tuple(n for n, t in BUILTIN.items() if t.view != "generic")


def _parse_marks(r: dict, where: str) -> tuple:
    from .config import ConfigError

    raw = r.get("marks")
    if raw is None:
        out = []
        if r.get("left_label"):
            out.append((float(r.get("min", 0.0)), str(r["left_label"])))
        if r.get("right_label"):
            out.append((float(r.get("max", 1.0)), str(r["right_label"])))
        return tuple(out)
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.scale.marks must map a number to a word, e.g. {{0: false, 1: true}}.")
    try:
        return tuple(sorted((float(k), str(v)) for k, v in raw.items()))
    except (TypeError, ValueError):
        raise ConfigError(f"{where}.scale.marks: keys must be numbers.") from None


def customise(base: TaskSpec, raw: dict, where: str) -> TaskSpec:
    """Apply a project's manifest overrides to a built-in spec."""
    from .config import ConfigError  # local import: config imports this module

    kw: dict = {}
    if raw.get("note_label"):
        kw["note_label"] = str(raw["note_label"])
    if raw.get("note_placeholder"):
        kw["note_placeholder"] = str(raw["note_placeholder"])

    if base.name == "choice":
        labels = raw.get("labels")
        if not isinstance(labels, list) or len(labels) < 2:
            raise ConfigError(f"{where}: task 'choice' needs a 'labels:' list with at least two entries.")
        parsed = []
        for i, lab in enumerate(labels):
            if isinstance(lab, bool) or (isinstance(lab, dict) and isinstance(lab.get("value"), bool)):
                raise ConfigError(
                    f"{where}.labels[{i}]: YAML read this label as true/false. Words like "
                    f"YES, NO, ON, OFF must be quoted: \"NO\".")
            if isinstance(lab, str):
                lab = {"value": lab}
            if not isinstance(lab, dict) or not lab.get("value"):
                raise ConfigError(f"{where}.labels[{i}] must be a string or a mapping with 'value'.")
            parsed.append(LabelSpec(str(lab["value"]), str(lab.get("text", lab["value"])),
                                    str(lab.get("help", "")), bool(lab.get("primary", False)),
                                    str(lab.get("glyph", "🔵"))))
        values = [p.value for p in parsed]
        if len(set(values)) != len(values):
            raise ConfigError(f"{where}: label values must be unique, got {values}.")
        kw["labels"] = tuple(parsed)
        kw["agreement_level"] = str(raw.get("agreement_level", "nominal"))
    if base.name == "rating":
        r = raw.get("scale") or {}
        if not isinstance(r, dict):
            raise ConfigError(f"{where}: 'scale' must be a mapping (min, max, step, ...).")
        try:
            spec = RatingSpec(
                minimum=float(r.get("min", 0.0)), maximum=float(r.get("max", 1.0)),
                step=float(r.get("step", 0.05)),
                default=(float(r["default"]) if r.get("default") is not None else None),
                left_label=str(r.get("left_label", "")), right_label=str(r.get("right_label", "")),
                unsure_label=str(r.get("unsure_label", "")),
                level=str(r.get("level", "interval")),
                marks=_parse_marks(r, where),
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{where}.scale: numbers expected ({exc}).") from None
        if not spec.minimum < spec.maximum or spec.step <= 0:
            raise ConfigError(f"{where}.scale: need min < max and step > 0.")
        if spec.level not in ("interval", "ordinal"):
            raise ConfigError(f"{where}.scale.level must be 'interval' or 'ordinal'.")
        kw["rating"] = spec
        kw["agreement_level"] = spec.level

    also = raw.get("also_accept")
    if also is not None:
        if not isinstance(also, list):
            raise ConfigError(f"{where}: 'also_accept' must be a list of earlier label values.")
        kw["also_accept"] = tuple(str(v) for v in also)

    reason = raw.get("note_required_for")
    if reason is not None:
        if not isinstance(reason, list):
            raise ConfigError(f"{where}: 'note_required_for' must be a list of label values.")
        kw["reason_required"] = tuple(str(v) for v in reason)

    if not kw:
        return base
    fields = {f: getattr(base, f) for f in base.__dataclass_fields__}
    fields.update(kw)
    return TaskSpec(**fields)
