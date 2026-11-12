"""
A one-time agreement people must accept before they can work in a project.

Put the agreement, as Markdown, in the project folder as ``agreement.md``. Each
person who is not an admin then sees it in a scrolling box, a check box and an Enter button
the first time they open the project. Accepting is final: it is written once to
``agreement_acceptances.json`` in the project folder (who, when, and a fingerprint
of the exact text they accepted) and is never changed or removed by the app.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import WorkspaceConfig

AGREEMENT = "agreement.md"
RECORDS = "agreement_acceptances.json"


def path(ws: WorkspaceConfig) -> Path:
    return ws.root / AGREEMENT


def text(ws: WorkspaceConfig) -> str | None:
    p = path(ws)
    return p.read_text(encoding="utf-8") if p.is_file() else None


def title(body: str) -> str:
    """The first heading, as written."""
    for line in body.splitlines():
        if line.strip().startswith("#"):
            return line.strip().lstrip("#").strip()
    return "Agreement"


def fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _records_path(ws: WorkspaceConfig) -> Path:
    return ws.root / RECORDS


def records(ws: WorkspaceConfig) -> dict:
    p = _records_path(ws)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")) or {}
    except (OSError, json.JSONDecodeError):
        return {}


def accepted(ws: WorkspaceConfig, name: str) -> bool:
    return name in records(ws)


def accept(ws: WorkspaceConfig, name: str) -> None:
    """Record acceptance once. An existing record is never overwritten."""
    body = text(ws)
    if body is None:
        raise ValueError("This project has no agreement.")
    lock = ws.root / ".agreement.lock"
    with lock.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            data = records(ws)
            if name in data:
                return
            data[name] = {"accepted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                          "agreement_sha256": fingerprint(body), "title": title(body)}
            target = _records_path(ws)
            tmp = target.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(target)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
