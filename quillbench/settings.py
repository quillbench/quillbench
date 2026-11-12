"""Studio-wide settings (``settings.yaml`` in the studio home). All keys are optional."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import SHORT_NAME, paths


@dataclass(frozen=True)
class StudioSettings:
    # The interface says only "QuillBench"; the full name is kept for the docs.
    title: str = SHORT_NAME
    subtitle: str = "Human annotation for research datasets"
    brand: str = "QuillBench"
    footnote: str = ""
    cookie_name: str = "quillbench_auth"
    session_max_age_days: int = 30
    display_row_cap: int = 300
    query_timeout_seconds: int = 30
    port: int = 8560
    base_url_path: str = "quillbench"
    backup_dir: Path = Path.home() / "quillbench_backups"
    backup_keep: int = 14
    # project ids hidden from annotators, per workspace: {workspace_id: [project_id, ...]}
    archived_projects: dict = field(default_factory=dict)
    # workspace ids hidden from annotators (their data is untouched)
    archived_workspaces: list = field(default_factory=list)
    # optional LDAP / Active Directory sign-in, see ldap_auth.py
    ldap: dict = field(default_factory=dict)
    # optional HTTPS served by QuillBench itself: paths to a certificate and its private key
    ssl_cert: str = ""
    ssl_key: str = ""


def load_settings(path: Path | None = None) -> StudioSettings:
    path = path or paths.settings_file()
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    known = {k: v for k, v in raw.items() if k in StudioSettings.__dataclass_fields__}
    if "backup_dir" in known:
        known["backup_dir"] = Path(str(known["backup_dir"])).expanduser()
    return StudioSettings(**known)


def save_archived_projects(archived: dict, path: Path | None = None) -> None:
    """Update only ``archived_projects`` and keep every other key as written."""
    path = path or paths.settings_file()
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw["archived_projects"] = {k: sorted(v) for k, v in archived.items() if v}
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def save_setting(key: str, value, path: Path | None = None) -> None:
    """Update one key in settings.yaml and keep every other key as written."""
    path = path or paths.settings_file()
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw[key] = value
    path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
