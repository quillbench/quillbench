"""
Backups: every workspace's answers and assignments, plus the studio home.

One ``.tar.gz`` per run in ``settings.backup_dir`` (mode 600), the newest
``backup_keep`` kept. Batch and benchmark files are not included -- they are
inputs that already live in the papers' own folders and version control; the
answers are what cannot be recreated.
"""

from __future__ import annotations

import hashlib
import tarfile
from datetime import datetime
from pathlib import Path

from . import paths, registry
from .settings import load_settings


def run(dest: Path | None = None, keep: int | None = None) -> Path:
    settings = load_settings()
    dest = Path(dest or settings.backup_dir)
    keep = settings.backup_keep if keep is None else keep
    dest.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = dest / f"labelwise_backup_{stamp}.tar.gz"
    workspaces, _ = registry.load_all(settings)
    tmp = target.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(paths.studio_home(), arcname="studio_home")
        for ws in workspaces:
            base = f"workspaces/{ws.id}"
            tar.add(ws.manifest, arcname=f"{base}/{ws.manifest.name}")
            if ws.assignments_file.exists():
                tar.add(ws.assignments_file, arcname=f"{base}/{ws.assignments_file.name}")
            for i, d in enumerate(ws.all_progress_dirs):
                if d.exists():
                    tar.add(d, arcname=f"{base}/progress_{i}_{d.parent.name}")
    tmp.chmod(0o600)
    tmp.rename(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_name(target.name + ".sha256").write_text(f"{digest}  {target.name}\n")
    old = sorted(dest.glob("labelwise_backup_*.tar.gz"))
    for stale in old[:-keep] if keep > 0 else []:
        stale.unlink(missing_ok=True)
        stale.with_name(stale.name + ".sha256").unlink(missing_ok=True)
    return target
