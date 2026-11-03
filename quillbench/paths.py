"""Where the studio keeps its own (non-paper) state.

Everything that belongs to a paper -- batches, progress, assignments, exports --
lives in that paper's workspace folder. The studio home only holds what is shared
across papers: the workspace registry, annotator identities, the session secret,
and studio settings. ``$QUILLBENCH_HOME`` overrides the location (used by the tests).
"""

from __future__ import annotations

import os
from pathlib import Path


def studio_home() -> Path:
    env = os.environ.get("QUILLBENCH_HOME") or os.environ.get("LABELWISE_HOME")
    home = Path(env).expanduser() if env else Path.home() / ".config" / "quillbench"
    old = Path.home() / ".config" / "labelwise"  # installs made before the rename
    if not env and not home.exists() and old.exists():
        home = old
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass
    return home


def registry_file() -> Path:
    return studio_home() / "registry.yaml"


def accounts_file() -> Path:
    return studio_home() / "accounts.json"


def secret_file() -> Path:
    return studio_home() / "app_secret"


def settings_file() -> Path:
    return studio_home() / "settings.yaml"


def key_file() -> Path:
    """Private mapping from real annotator ids to the codes used in exports."""
    return studio_home() / "annotator_codes.json"
