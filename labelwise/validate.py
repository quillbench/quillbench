"""
``labelwise validate``: check that every declared input exists and is well-formed.

Eager on purpose: a missing benchmark CSV must be a loud message before
annotators sign in, never a blank annotation screen later. Problems are
collected, not raised, so one broken project does not hide the others. Nothing
is created or changed on disk.
"""

from __future__ import annotations

import json
import os

import pandas as pd

from .accounts import load_assignments
from .config import REQUIRED_BENCHMARK_COLUMNS, ConfigError, WorkspaceConfig, validate_gold_cache_mapping
from .storage import BATCH_SUFFIXES, progress_files


def _read_head(path, nrows=5) -> pd.DataFrame:
    if str(path).endswith(".jsonl"):
        return pd.read_json(path, lines=True, nrows=nrows)
    return pd.read_csv(path, nrows=nrows)


def validate_workspace(ws: WorkspaceConfig, users: dict | None = None) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    for d in ws.all_batches_dirs:
        if d.exists() and not d.is_dir():
            errors.append(f"batches folder is not a folder: {d}")
    for proj in ws.projects:
        d = ws.batches_dir_for(proj)
        if not d.exists():
            errors.append(f"project '{proj.id}': batches folder does not exist: {d}")

    batch_files = sorted(f for d in ws.all_batches_dirs if d.exists()
                         for s in BATCH_SUFFIXES for f in d.glob(f"*{s}"))
    if not batch_files:
        warnings.append(f"No batch files (.csv or .jsonl) in {[str(d) for d in ws.all_batches_dirs]}.")

    for proj in ws.projects:
        where = f"project '{proj.id}'"
        spec = proj.task_spec
        if proj.benchmark_csv is not None:
            if not proj.benchmark_csv.exists():
                errors.append(f"{where}: benchmark_csv does not exist: {proj.benchmark_csv}")
            elif spec.view != "generic":
                try:
                    head = pd.read_csv(proj.benchmark_csv, nrows=5)
                    missing = [c for c in REQUIRED_BENCHMARK_COLUMNS if c not in head.columns]
                    if missing:
                        errors.append(f"{where}: benchmark_csv is missing column(s) {missing}: "
                                      f"{proj.benchmark_csv}")
                except Exception as exc:
                    errors.append(f"{where}: benchmark_csv could not be read ({exc})")

        if proj.gold_cache_file is not None:
            f = proj.gold_cache_file
            if not f.exists() or not os.access(f, os.R_OK):
                errors.append(f"{where}: gold_cache.file is missing or unreadable: {f}")
            elif f.stat().st_size < 50_000_000:
                try:
                    validate_gold_cache_mapping(json.loads(f.read_text(encoding="utf-8")), where,
                                                f"gold_cache.file {f}")
                except (ValueError, ConfigError) as exc:
                    errors.append(f"{where}: gold_cache.file is not usable: {exc}")
            else:
                warnings.append(f"{where}: gold_cache.file is {f.stat().st_size / 1e6:.0f} MB; "
                                f"consider sharding it (python -m labelwise.shard_gold_cache).")
        elif proj.gold_cache_shard_dir is not None:
            d = proj.gold_cache_shard_dir
            if not d.is_dir():
                errors.append(f"{where}: gold_cache.shard_dir does not exist: {d}")
            else:
                mine = [b.stem for b in batch_files
                        if _routes(ws, b.stem) == proj.id]
                missing = [b for b in mine if not proj.shard_path(b).exists()]
                if missing:
                    warnings.append(f"{where}: no gold-cache shard for {missing[:5]}"
                                    f"{'...' if len(missing) > 5 else ''}")

        if proj.jury is not None:
            if not proj.jury.csv_path.exists():
                errors.append(f"{where}: jury_results.csv does not exist: {proj.jury.csv_path}")
        if proj.graph_db is not None and not proj.graph_db.password_available:
            warnings.append(f"{where}: ${proj.graph_db.password_env} is unset; the Query Console "
                            f"cannot reach {proj.graph_db.uri} (annotation is unaffected).")
        for item_id, expected in proj.checks.items():
            if spec.kind == "labels" and str(expected) not in spec.values:
                errors.append(f"{where}: check item '{item_id}' expects '{expected}', "
                              f"which is not one of {list(spec.values)}")

    for bf in batch_files:
        try:
            ws.project_for_batch(bf.stem)
        except ConfigError as exc:
            warnings.append(str(exc))
            continue
        try:
            if "id" not in _read_head(bf).columns:
                errors.append(f"batch {bf.name}: has no 'id' column")
        except Exception as exc:
            errors.append(f"batch {bf.name}: could not be read ({exc})")

    stems = {b.stem for b in batch_files}
    assignments = load_assignments(ws)
    for name, rec in assignments.items():
        if users is not None and name not in users:
            warnings.append(f"assignments: '{name}' has batches here but no account")
        unknown = [b for b in rec.get("batches", []) if b not in stems]
        if unknown:
            warnings.append(f"assignments: '{name}' is assigned missing batch(es) {unknown}")

    unknown_values = 0
    for pf in progress_files(ws):
        parts = pf.stem.split("__", 1)
        if len(parts) != 2:
            warnings.append(f"progress file {pf.name}: name is not '<annotator>__<batch>.csv'")
            continue
        proj_id = _routes(ws, parts[1])
        if proj_id is None:
            continue
        spec = ws.project_by_id(proj_id).task_spec
        try:
            vals = pd.read_csv(pf, dtype=str, keep_default_na=False).get("human_verdict")
        except Exception as exc:
            warnings.append(f"progress file {pf.name}: could not be read ({exc})")
            continue
        if vals is None:
            warnings.append(f"progress file {pf.name}: no human_verdict column")
            continue
        unknown_values += int(sum(1 for v in vals if v.strip() and not spec.is_labeled(v)))
    if unknown_values:
        warnings.append(f"{unknown_values} recorded answers are not valid for their project's task "
                        f"and are not counted (add them to 'also_accept' if they should be).")
    return errors, warnings


def _routes(ws: WorkspaceConfig, batch: str) -> str | None:
    try:
        return ws.project_for_batch(batch).id
    except ConfigError:
        return None
