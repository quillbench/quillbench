"""
The workspace registry: which paper workspaces this studio serves.

``registry.yaml`` in the studio home lists manifest paths only::

    workspaces:
      - /path/to/paper_one/quillbench_workspace.yaml
      - /path/to/paper_two/quillbench_workspace.yaml

The studio reads each manifest when it loads and writes answers straight into
that paper's folders, so the software and the papers stay separate yet always
in sync: there is nothing to copy back and forth.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import yaml

from . import paths
from .config import ConfigError, WorkspaceConfig, load_workspace, manifest_in
from .settings import StudioSettings, load_settings


def _normalise(manifest: str | Path) -> Path:
    path = Path(manifest).expanduser().resolve()
    return manifest_in(path) if path.is_dir() else path


def manifest_paths(path: Path | None = None) -> list[Path]:
    path = path or paths.registry_file()
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [Path(str(p)).expanduser() for p in raw.get("workspaces", []) or []]


def _write(manifests: list[Path], path: Path | None = None) -> None:
    path = path or paths.registry_file()
    path.write_text(
        yaml.safe_dump({"workspaces": [str(p) for p in manifests]}, sort_keys=False),
        encoding="utf-8",
    )
    path.chmod(0o600)


def add(manifest: str | Path, path: Path | None = None) -> WorkspaceConfig:
    """Register a workspace after checking that its manifest loads."""
    target = _normalise(manifest)
    ws = load_workspace(target)
    current = manifest_paths(path)
    if any(_normalise(p) == target for p in current):
        return ws
    for p in current:
        try:
            if load_workspace(p).id == ws.id:
                raise ConfigError(f"A workspace with id '{ws.id}' is already registered ({p}).")
        except ConfigError as exc:
            if "already registered" in str(exc):
                raise
    _write(current + [target], path)
    return ws


def remove(manifest_or_id: str, path: Path | None = None) -> bool:
    """Unregister a workspace (its files are untouched)."""
    current = manifest_paths(path)
    keep = []
    for p in current:
        try:
            ws_id = load_workspace(p).id
        except ConfigError:
            ws_id = None
        if manifest_or_id in (ws_id, str(p)) or _normalise(manifest_or_id) == _normalise(p):
            continue
        keep.append(p)
    _write(keep, path)
    return len(keep) != len(current)


def load_all(settings: StudioSettings | None = None) -> tuple[list[WorkspaceConfig], list[str]]:
    """Every registered workspace that loads, plus one message per one that doesn't.

    A broken manifest in one paper must never take the other papers offline.
    """
    settings = settings or load_settings()
    loaded: list[WorkspaceConfig] = []
    problems: list[str] = []
    for manifest in manifest_paths():
        try:
            ws = load_workspace(
                manifest,
                display_row_cap=settings.display_row_cap,
                query_timeout_seconds=settings.query_timeout_seconds,
                archived_projects=(settings.archived_projects or {}).get(
                    _peek_id(manifest), ()),
            )
        except ConfigError as exc:
            problems.append(str(exc))
            continue
        if ws.id in (settings.archived_workspaces or []):
            ws = replace(ws, archived=True)
        loaded.append(ws)
    return loaded, problems


def _peek_id(manifest: Path) -> str:
    try:
        raw = yaml.safe_load(_normalise(manifest).read_text(encoding="utf-8")) or {}
        return str((raw.get("workspace") or {}).get("id", ""))
    except Exception:
        return ""
