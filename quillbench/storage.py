"""
Data access: items, gold caches (whole or sharded), batches and progress files.

Progress files stay in the workspace as plain CSV --
``<progress_dir>/<annotator>__<batch>.csv`` with ``human_verdict``, ``note`` and
``paraphrase`` columns -- so existing annotation records read unchanged. QuillBench adds
two columns when an item is answered, ``updated_at`` (UTC, ISO 8601) and
``seconds`` (time the item was open), and writes every file atomically.

Batch files may be CSV or JSON Lines (one object per line, with an ``id`` key).

Caching uses plain path strings plus mtime as keys, so edits on disk are picked
up and ``st.cache_data`` hashing stays cheap.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from .config import ProjectConfig, WorkspaceConfig, validate_gold_cache_mapping

PROGRESS_COLUMNS = ("human_verdict", "note", "paraphrase", "updated_at", "seconds")
BATCH_SUFFIXES = (".csv", ".jsonl")

# ──────────────────────────────────────────────────────────────────────────
# NaN-safe display text
# ──────────────────────────────────────────────────────────────────────────
def blank_if_nan(value: Any) -> str:
    """``str(value)`` with pandas-NaN cells rendered as ``''``.

    This is an EXACT-match check, never a substring replace. The predecessor
    of this function used ``str(v).replace("nan", "")``, which strips the
    substring "nan" from *anywhere* in the text: real content containing
    "nan" was silently corrupted — Japanese place names ending in "-nan"
    (Sennan, Kainan, Konan, Kyonan, Nichinan, Shunan, Unnan, Anan, Hannan)
    were shown to annotators with the suffix eaten ("Sennan" -> "Sen"),
    producing a wave of spurious INCORRECT verdicts against gold queries that
    were in fact correct. Found and fixed 2026-07-31.

    Rule of thumb for anyone editing this file: ``.replace()`` on
    user-facing text is a bug until proven otherwise. Use exact comparisons.

    Every flavour of "missing" renders blank, not as its repr. ``None``,
    ``pd.NA`` and ``NaT`` used to fall through to ``str()`` and show up in
    the UI as the literal text "None"/"<NA>"/"NaT" — which an annotator
    reasonably reads as real cell content. Only ``float('nan')`` was handled,
    because it is the only one whose ``str()`` happens to be "nan".
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        # pd.isna over a list/array/dict returns an array or raises; those
        # are real values, so fall through to str().
        pass
    text = str(value).strip()
    return "" if text == "nan" else text


def blank_nan_series(series: pd.Series) -> pd.Series:
    """Vectorized :func:`blank_if_nan` for a whole column."""
    filled = series.fillna("").astype(str).str.strip()
    return filled.where(filled != "nan", "")


# ──────────────────────────────────────────────────────────────────────────
# Cached readers (keyed by path string, not by config object)
# ──────────────────────────────────────────────────────────────────────────
@st.cache_data(show_spinner=False)
def _read_csv(path_str: str, mtime: float) -> pd.DataFrame:
    """Read a CSV or JSON Lines file, cache-busted by mtime."""
    if path_str.endswith(".jsonl"):
        return pd.read_json(path_str, lines=True, dtype=False)
    return pd.read_csv(path_str)


@st.cache_data(show_spinner=False)
def _read_json(path_str: str, mtime: float) -> dict:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def load_benchmark(project: ProjectConfig) -> pd.DataFrame:
    """The project's full benchmark CSV (empty for generic projects without one)."""
    if project.benchmark_csv is None:
        return pd.DataFrame()
    return _read_csv(str(project.benchmark_csv), _mtime(project.benchmark_csv))


def load_gold_cache(project: ProjectConfig, batch_name: str) -> dict:
    """Precomputed gold-query results for one batch.

    For a sharded project only that batch's shard is read. This is the
    single most important performance property of the tool: full gold caches
    reach hundreds of megabytes, and parsing one on every batch view blocked
    the Streamlit event loop long enough to trip a genuine race in
    Streamlit's own WebSocket reconnect handling ("Bad message format: tried
    to initialize SessionInfo before it was initialized"), which annotators
    on flaky mobile/SSH-tunnel connections hit constantly.

    A missing shard is a recoverable, explainable state (the operator has not
    run the sharding script for a new batch yet), so it returns ``{}`` and the
    UI says so — unlike a missing *configured* path, which fails at startup.

    Entry shape is validated here (``{id: {...}}``). A shard is written by a
    script, so a malformed one is an operator error that must be reported as
    such, not left to surface as ``AttributeError: 'str' object has no
    attribute 'get'`` inside the result panel the moment an annotator opens
    the one bad item. Sharded caches and oversized ones skip startup
    validation entirely, so this is the only place they get checked.
    """
    if project.sharded:
        shard = project.shard_path(batch_name)
        if not shard.exists():
            return {}
        return validate_gold_cache_mapping(
            _read_json(str(shard), _mtime(shard)),
            f"project '{project.id}', batch '{batch_name}'",
            shard,
        )
    if project.gold_cache_file is None:
        # A results-withheld project (adjudication, pair_semantics) may configure no
        # cache at all: config.py permits gold_cache to be absent for those tasks, and
        # their views never show a result. There is nothing to read or validate.
        return {}
    return validate_gold_cache_mapping(
        _read_json(str(project.gold_cache_file), _mtime(project.gold_cache_file)),
        f"project '{project.id}'",
        project.gold_cache_file,
    )


def gold_cache_missing_reason(project: ProjectConfig, batch_name: str) -> str | None:
    """Human-readable explanation when a batch has no gold cache available."""
    if not project.sharded:
        return None
    shard = project.shard_path(batch_name)
    if shard.exists():
        return None
    return (
        f"No precomputed result shard for batch '{batch_name}'. Expected file: "
        f"{shard}. Regenerate with: "
        f"python -m quillbench.shard_gold_cache --project {project.id}"
    )


def load_jury(project: ProjectConfig) -> pd.DataFrame:
    if project.jury is None:
        return pd.DataFrame()
    return _read_csv(str(project.jury.csv_path), _mtime(project.jury.csv_path))


# ──────────────────────────────────────────────────────────────────────────
# Batches and progress
# ──────────────────────────────────────────────────────────────────────────
def batch_path(ws: WorkspaceConfig, batch_name: str) -> Path:
    project = ws.project_for_batch(batch_name)
    folder = ws.batches_dir_for(project)
    for suffix in BATCH_SUFFIXES:
        candidate = folder / f"{batch_name}{suffix}"
        if candidate.exists():
            return candidate
    return folder / f"{batch_name}.csv"


def load_batch(ws: WorkspaceConfig, batch_name: str) -> pd.DataFrame:
    """A batch slice. Empty DataFrame if the file is absent (caller reports)."""
    path = batch_path(ws, batch_name)
    if not path.exists():
        return pd.DataFrame()
    return _read_csv(str(path), _mtime(path))


def list_batches(ws: WorkspaceConfig) -> list[str]:
    names: set[str] = set()
    for d in ws.all_batches_dirs:
        if d.exists():
            for suffix in BATCH_SUFFIXES:
                names.update(p.stem for p in d.glob(f"*{suffix}"))
    return sorted(names)


def progress_path(ws: WorkspaceConfig, annotator: str, batch_name: str) -> Path:
    project = ws.project_for_batch(batch_name)
    return ws.progress_dir_for(project) / f"{annotator}__{batch_name}.csv"


def load_progress(ws: WorkspaceConfig, annotator: str, batch_name: str) -> pd.DataFrame:
    """Saved answers. Absent file = nothing labeled yet; empty frame is correct."""
    path = progress_path(ws, annotator, batch_name)
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def get_progress_df(
    ws: WorkspaceConfig, annotator: str, batch_name: str, batch_df: pd.DataFrame
) -> pd.DataFrame:
    """Batch rows merged with this annotator's recorded answers and notes."""
    prog = load_progress(ws, annotator, batch_name)
    df = batch_df.copy()
    for col in PROGRESS_COLUMNS:
        if col not in df.columns:
            df[col] = ""
    if not prog.empty and "id" in prog.columns:
        prog = prog.drop_duplicates("id", keep="last")
        for col in PROGRESS_COLUMNS:
            if col in prog.columns:
                mapping = prog.set_index("id")[col].to_dict()
                df[col] = df["id"].map(mapping).fillna("")
    for col in PROGRESS_COLUMNS:
        df[col] = blank_nan_series(df[col])
    return df


def save_progress(ws: WorkspaceConfig, annotator: str, batch_name: str, df: pd.DataFrame) -> None:
    """Write atomically: a crash mid-write must never leave half a progress file."""
    import os
    import tempfile

    path = progress_path(ws, annotator, batch_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, index=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def set_verdict(
    ws: WorkspaceConfig,
    annotator: str,
    batch_name: str,
    query_id: Any,
    verdict: str,
    note: str,
    batch_df: pd.DataFrame,
    paraphrase: str | None = None,
    seconds: float | None = None,
) -> None:
    df = get_progress_df(ws, annotator, batch_name, batch_df)
    mask = df["id"] == query_id
    df.loc[mask, "human_verdict"] = str(verdict)
    df.loc[mask, "note"] = note or ""
    if paraphrase is not None:
        df.loc[mask, "paraphrase"] = paraphrase or ""
    df.loc[mask, "updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if seconds is not None:
        df.loc[mask, "seconds"] = f"{max(0.0, float(seconds)):.1f}"
    save_progress(ws, annotator, batch_name, df)


def labeled_mask(df: pd.DataFrame, project: ProjectConfig) -> pd.Series:
    spec = project.task_spec
    return df["human_verdict"].apply(spec.is_labeled)


def batch_status(ws: WorkspaceConfig, annotator: str, batch_name: str) -> dict:
    batch_df = load_batch(ws, batch_name)
    if batch_df.empty or "id" not in batch_df.columns:
        return {"total": 0, "labeled": 0, "pct": 0}
    project = ws.project_for_batch(batch_name)
    df = get_progress_df(ws, annotator, batch_name, batch_df)
    total = len(df)
    labeled = int(labeled_mask(df, project).sum())
    return {"total": total, "labeled": labeled, "pct": round(labeled / total * 100) if total else 0}


def progress_files(ws: WorkspaceConfig) -> list[Path]:
    files: list[Path] = []
    for d in ws.all_progress_dirs:
        if d.exists():
            files.extend(d.glob("*.csv"))
    return sorted(set(files))


def progress_summary_rows(ws: WorkspaceConfig) -> tuple[list[dict], list[str]]:
    """One row per annotator x batch progress file, plus a list of problems.

    A half-written or hand-edited progress file must not take down the whole
    supervisor view -- but it must not vanish from it in silence either. Skipped
    files are returned as warnings for the caller to surface.
    """
    rows: list[dict] = []
    problems: list[str] = []
    for pf in progress_files(ws):
        parts = pf.stem.split("__", 1)
        if len(parts) != 2:
            problems.append(f"{pf.name}: filename is not '<annotator>__<batch>.csv'; skipped.")
            continue
        annotator, batch_name = parts
        try:
            project = ws.project_for_batch(batch_name)
        except Exception as exc:
            problems.append(f"{pf.name}: {exc}")
            continue
        if ws.progress_dir_for(project).resolve() != pf.parent.resolve():
            # A file in a shared folder that routes to a project writing elsewhere
            # (e.g. an old run left behind) is not that project's record.
            continue
        try:
            df = pd.read_csv(pf)
            answers = df["human_verdict"]
            mask = answers.apply(project.task_spec.is_labeled)
            labeled = int(mask.sum())
            total = len(df)
            counts = answers[mask].astype(str).str.strip().value_counts()
            if project.task_spec.kind == "labels":
                dist = ", ".join(f"{k} {v}" for k, v in counts.items())
            elif project.task_spec.kind == "rating":
                nums = pd.to_numeric(answers[mask], errors="coerce").dropna()
                dist = f"mean {nums.mean():.2f}" if len(nums) else ""
            else:
                dist = ""
            secs = pd.to_numeric(df.get("seconds", pd.Series(dtype=float)), errors="coerce").dropna()
            rows.append({
                "Annotator": annotator,
                "Project": project.name,
                "Batch": batch_name,
                "Labeled": labeled,
                "Total": total,
                "%": round(labeled / total * 100) if total else 0,
                "Answers": dist,
                "Median s/item": round(float(secs.median()), 1) if len(secs) else None,
                "Last modified": datetime.fromtimestamp(pf.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                "_file": str(pf),
                "_project": project.id,
            })
        except KeyError as exc:
            problems.append(f"{pf.name}: missing required column {exc}; skipped.")
        except Exception as exc:
            problems.append(f"{pf.name}: could not be read ({type(exc).__name__}: {exc}); skipped.")
    return rows, problems


def find_step(
    n: int, current: int, labeled: pd.Series, forward: bool, only_unlabeled: bool
) -> int:
    """Next row index in the requested direction, honoring the skip filter.

    The filter applies to stepping only, never to arbitrary jumps -- the item
    navigator can always land on any item. Clamps at both ends.
    """
    step = 1 if forward else -1
    i = current + step
    while 0 <= i < n:
        if not only_unlabeled or not labeled.iloc[i]:
            return i
        i += step
    return current


def jsonify_complex_cells(df: pd.DataFrame) -> pd.DataFrame:
    """Render list/dict cells as JSON text.

    Graph drivers return nested lists and maps; the interactive grid stringifies
    those through JS ``toString()`` and shows "[object Object]".
    """
    out = df.copy()
    for col in out.columns:
        if out[col].apply(lambda v: isinstance(v, (list, dict))).any():
            out[col] = out[col].apply(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
    return out
