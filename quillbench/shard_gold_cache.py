#!/usr/bin/env python3
"""
Split a project's full gold-result cache into one small file per batch.

Why this exists
---------------
A gold cache holds the precomputed result of every gold query in a benchmark
and can easily reach hundreds of megabytes. Parsing the whole thing to render
a single batch blocks the Streamlit event loop for seconds at a time, which
is not merely slow: in the deployment this tool was generalized from it was
long enough to trip a race inside Streamlit's own WebSocket reconnect
handling ("Bad message format: tried to initialize SessionInfo before it was
initialized"), and annotators on mobile or SSH-tunnelled connections hit it
constantly. Each batch needs only its own few hundred ids, so shard once and
let the app read only the shard it needs.

Run this after creating or changing batches, or after regenerating the gold
cache. Shard files are derived data — regenerable, and safe to gitignore.

Usage
-----
    python -m quillbench.shard_gold_cache WORKSPACE              # all sharded projects
    python -m quillbench.shard_gold_cache WORKSPACE --project demo
    python -m quillbench.shard_gold_cache WORKSPACE --source /path/to/full_cache.json

WORKSPACE is a registered workspace id or a manifest path. For a project with
``gold_cache.shard_dir``, the full source cache is taken from
``gold_cache.source_file`` in the manifest, or from --source.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import yaml

from .config import ConfigError, load_workspace


def _source_for(cfg_path: Path, project_id: str, override: str | None) -> Path | None:
    """Locate the full cache to shard from.

    ``source_file`` is not part of ProjectConfig (the app never reads the full
    cache — that is the whole point), so it is read straight from the YAML.
    """
    if override:
        return Path(override).expanduser()
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    for proj in raw.get("projects", []):
        if str(proj.get("id")) == project_id:
            source = (proj.get("gold_cache") or {}).get("source_file")
            if not source:
                return None
            path = Path(str(source)).expanduser()
            return path if path.is_absolute() else (cfg_path.parent / path)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("workspace", help="Workspace id or manifest path")
    parser.add_argument("--project", default=None, help="Shard only this project id")
    parser.add_argument("--source", default=None, help="Full gold-cache JSON to shard")
    parser.add_argument(
        "--force", action="store_true", help="Rewrite shards that already exist"
    )
    args = parser.parse_args()

    try:
        from . import registry

        known = {w.id: w for w in registry.load_all()[0]}
        cfg = known.get(args.workspace) or load_workspace(args.workspace)
        cfg_path = cfg.manifest
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    projects = [p for p in cfg.projects if p.sharded]
    if args.project:
        projects = [p for p in projects if p.id == args.project]
        if not projects:
            print(
                f"ERROR: no project '{args.project}' with a gold_cache.shard_dir "
                f"in {cfg_path}",
                file=sys.stderr,
            )
            return 2
    if not projects:
        print("No projects use a sharded gold cache; nothing to do.")
        return 0

    exit_code = 0
    for project in projects:
        source = _source_for(cfg_path, project.id, args.source)
        if source is None:
            print(
                f"ERROR: project '{project.id}' has no gold_cache.source_file in "
                f"the config and no --source was given.",
                file=sys.stderr,
            )
            exit_code = 2
            continue
        if not source.exists():
            print(f"ERROR: project '{project.id}': source not found: {source}",
                  file=sys.stderr)
            exit_code = 2
            continue

        out_dir = project.gold_cache_shard_dir
        assert out_dir is not None
        out_dir.mkdir(parents=True, exist_ok=True)

        size_mb = source.stat().st_size / 1e6
        print(f"[{project.id}] loading {source} ({size_mb:.1f} MB)…")
        full = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(full, dict):
            print(
                f"ERROR: project '{project.id}': {source} must be a JSON object "
                f"mapping query id -> result.",
                file=sys.stderr,
            )
            exit_code = 2
            continue
        print(f"[{project.id}] {len(full):,} cached results")

        pattern = f"{project.batch_prefix}*.csv" if project.batch_prefix else "*.csv"
        proj_batches_dir = cfg.batches_dir_for(project)
        batch_files = sorted(proj_batches_dir.glob(pattern))
        if not batch_files:
            print(f"[{project.id}] WARNING: no batches match {pattern} in "
                  f"{proj_batches_dir}")
        for batch_file in batch_files:
            out_path = project.shard_path(batch_file.stem)
            if out_path.exists() and not args.force:
                print(f"  {batch_file.stem}: shard exists, skipping (--force to rewrite)")
                continue
            df = pd.read_csv(batch_file)
            if "id" not in df.columns:
                print(f"  WARNING {batch_file.name}: no 'id' column, skipped")
                continue
            ids = df["id"].astype(str).tolist()
            shard = {i: full[i] for i in ids if i in full}
            missing = [i for i in ids if i not in full]
            if missing:
                print(
                    f"  WARNING {batch_file.name}: {len(missing)} id(s) absent from "
                    f"the cache (e.g. {missing[:5]})"
                )
            out_path.write_text(json.dumps(shard), encoding="utf-8")
            print(
                f"  {batch_file.stem}: {len(shard)} ids -> {out_path.name} "
                f"({out_path.stat().st_size / 1e6:.2f} MB)"
            )

    print("Done.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
