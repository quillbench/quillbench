"""Create projects from the web interface.

Each project made here is its own workspace folder under the studio home, so it
never touches a workspace that already exists: nothing here edits another
paper's manifest, batches, progress or assignments.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pandas as pd
import yaml

from . import accounts, paths, registry
from .config import MANIFEST_NAME, ConfigError, WorkspaceConfig, load_workspace

BATCH_PREFIX = "batch_"
TASKS = {"choice": "Label buttons", "rating": "Slider rating", "text": "Short written answer"}


def projects_root() -> Path:
    return paths.studio_home() / "projects"


def is_app_project(ws: WorkspaceConfig) -> bool:
    return projects_root().resolve() in ws.root.parents


def clean_labels(text: str) -> list[str]:
    """One label per comma or line, duplicates dropped, order kept."""
    return list(dict.fromkeys(s.strip() for s in re.split(r"[,\n]", text) if s.strip()))


def read_table(source) -> pd.DataFrame:
    df = pd.read_csv(source, dtype=str, keep_default_na=False)
    if df.empty:
        raise ValueError("The CSV has no rows.")
    return df


def with_ids(df: pd.DataFrame, id_column: str | None, start: int = 1) -> pd.DataFrame:
    """Give the table the ``id`` column batches need, unique within it."""
    df = df.copy()
    if id_column and id_column != "id":
        df = df.drop(columns=["id"], errors="ignore").rename(columns={id_column: "id"})
    elif not id_column:
        df = df.drop(columns=["id"], errors="ignore")
        df.insert(0, "id", [str(i) for i in range(start, start + len(df))])
    if df["id"].astype(str).str.strip().eq("").any():
        raise ValueError("Some rows have an empty id.")
    repeats = df["id"][df["id"].duplicated()].unique().tolist()
    if repeats:
        raise ValueError(f"These ids appear more than once: {repeats[:5]}")
    return df


def split(df: pd.DataFrame, size: int, shuffle: bool = False, seed: int = 42) -> list[pd.DataFrame]:
    """Cut a table into batches of ``size`` rows; 0 keeps it whole."""
    if shuffle:
        df = df.sample(frac=1, random_state=seed).reset_index(drop=True)
    size = size if size > 0 else len(df)
    return [df.iloc[i:i + size] for i in range(0, len(df), size)]


def _existing(ws_root: Path) -> list[Path]:
    return sorted((ws_root / "batches").glob(f"{BATCH_PREFIX}*.csv"))


def _write_batches(root: Path, parts: list[pd.DataFrame]) -> list[str]:
    taken = [int(m.group(1)) for p in _existing(root)
             if (m := re.fullmatch(rf"{BATCH_PREFIX}(\d+)", p.stem))]
    number = max(taken, default=0)
    names = []
    for part in parts:
        number += 1
        name = f"{BATCH_PREFIX}{number:03d}"
        part.to_csv(root / "batches" / f"{name}.csv", index=False)
        names.append(name)
    return names


def assign(ws: WorkspaceConfig, batches: list[str], people: list[str], per_batch: int = 1) -> None:
    """Deal batches out in turn, each to ``per_batch`` different people."""
    if not people:
        return
    data = accounts.load_assignments(ws)
    per_batch = min(max(per_batch, 1), len(people))
    for i, batch in enumerate(batches):
        for k in range(per_batch):
            person = people[(i * per_batch + k) % len(people)]
            queue = data.setdefault(person, {"batches": []})["batches"]
            if batch not in queue:
                queue.append(batch)
    accounts.save_assignments(ws, data)


def _new_id(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "project"
    taken = {p.name for p in projects_root().glob("*")}
    for manifest in registry.manifest_paths():
        taken.add(registry._peek_id(manifest))
    found, n = base, 1
    while found in taken:
        n += 1
        found = f"{base}_{n}"
    return found


def create_project(name: str, task: str, instructions: str, table: pd.DataFrame | None,
                   display: list[str], owner: str, batch_size: int = 0, shuffle: bool = False,
                   labels: list[str] | None = None, scale: dict | None = None,
                   people: list[str] | None = None, per_batch: int = 1) -> WorkspaceConfig:
    name = name.strip()
    if not name:
        raise ValueError("Give the project a name.")
    if task not in TASKS:
        raise ValueError(f"Unknown task '{task}'.")
    if task == "choice" and len(labels or []) < 2:
        raise ValueError("Add at least two labels.")
    if task == "rating" and not (scale and scale["min"] < scale["max"] and scale["step"] > 0):
        raise ValueError("The slider needs a minimum below its maximum and a step above 0.")
    if table is not None and not display:
        raise ValueError("Choose at least one column to show.")

    ws_id = _new_id(name)
    project = {"id": ws_id, "name": name, "task": task, "default": True,
               "batch_prefix": BATCH_PREFIX, "display": list(display or []),
               "instructions": instructions.strip()}
    if task == "choice":
        project["labels"] = list(labels)
    if task == "rating":
        project["scale"] = scale
    manifest = {"workspace": {"id": ws_id, "name": name, "owner": owner,
                              "batches_dir": "batches", "progress_dir": "progress"},
                "projects": [project]}
    root = projects_root() / ws_id
    (root / "batches").mkdir(parents=True)
    try:
        (root / MANIFEST_NAME).write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
                                          encoding="utf-8")
        batches = [] if table is None else _write_batches(root, split(table, batch_size, shuffle))
        registry.add(root)
    except (ConfigError, OSError, ValueError):
        shutil.rmtree(root, ignore_errors=True)
        raise
    ws = load_workspace(root)
    assign(ws, batches, people or [], per_batch)
    return ws


def _update_project(ws: WorkspaceConfig, **fields) -> None:
    raw = yaml.safe_load(ws.manifest.read_text(encoding="utf-8"))
    raw["projects"][0].update(fields)
    ws.manifest.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def add_data(ws: WorkspaceConfig, table: pd.DataFrame, id_column: str | None, batch_size: int = 0,
             shuffle: bool = False, people: list[str] | None = None, per_batch: int = 1,
             display: list[str] | None = None) -> list[str]:
    """Add rows to a project made here, as new batches; existing batches are untouched.

    ``display`` picks the columns to show when the project has none chosen yet.
    """
    seen: list[str] = []
    for path in _existing(ws.root):
        seen += pd.read_csv(path, dtype=str, keep_default_na=False)["id"].tolist()
    new = with_ids(table, id_column, start=len(seen) + 1)
    clash = sorted(set(new["id"]) & set(seen))
    if clash:
        raise ValueError(f"These ids are already in the project: {clash[:5]}")
    shown = [f.column for f in ws.projects[0].display]
    missing = [c for c in shown if c not in new.columns]
    if missing:
        raise ValueError(f"The CSV is missing the column(s) annotators see: {missing}")
    if display and not shown:
        _update_project(ws, display=list(display))
    batches = _write_batches(ws.root, split(new, batch_size, shuffle))
    assign(ws, batches, people or [], per_batch)
    return batches


def add_labels(ws: WorkspaceConfig, new: list[str]) -> None:
    """Add label buttons to a project made here. Labels are never removed: saved answers use them."""
    if ws.projects[0].task != "choice":
        raise ValueError("Only label-button projects have labels.")
    current = yaml.safe_load(ws.manifest.read_text(encoding="utf-8"))["projects"][0]["labels"]
    _update_project(ws, labels=list(dict.fromkeys(current + new)))


def create_benchmark_project(name: str, connection_id: str, owner: str,
                             instructions: str = "", participants: list[str] | None = None):
    """A project where people write new benchmark items; see benchmark.py."""
    from . import benchmark, connections

    name = name.strip()
    if not name:
        raise ValueError("Give the project a name.")
    conn = connections.get(connection_id)
    if conn is None:
        raise ValueError("Choose a database connection (add one under Admin → Connections).")
    ws_id = _new_id(name)
    project = {"id": ws_id, "name": name, "task": "create", "default": True,
               "instructions": instructions.strip()}
    manifest = {"workspace": {"id": ws_id, "name": name, "owner": owner,
                              "batches_dir": "batches", "progress_dir": "progress"},
                "projects": [project]}
    root = projects_root() / ws_id
    (root / "batches").mkdir(parents=True)
    try:
        (root / MANIFEST_NAME).write_text(yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True),
                                          encoding="utf-8")
        ws = load_workspace(root)
        form = benchmark.Form(connection=connection_id, participants=list(participants or []),
                              slots=benchmark.default_slots(conn.language))
        benchmark.save_form(ws, form)
        registry.add(root)
    except (ConfigError, OSError, ValueError):
        shutil.rmtree(root, ignore_errors=True)
        raise
    return load_workspace(root)
