"""
Accounts, assignments and session persistence.

Two files, two owners:

* **Identities** -- who a person is -- live once, in the studio home
  (``accounts.json``, mode 600)::

      {"users": {"alice": {"password_hash": "pbkdf2_sha256$...", "role": "annotator",
                           "display_name": "Alice", "active": true}}}

  Passwords are stored as salted PBKDF2-SHA256 hashes (standard library, no
  extra dependency).

* **Assignments** -- what a person works on in a paper -- live in that paper's
  workspace (``labelwise_assignments.json``), so they travel with the paper's data and
  contain no secrets::

      {"alice": {"batches": ["demo_001", "demo_002"]}}

Session persistence uses an HMAC-signed cookie rather than session state alone:
annotators work over flaky mobile and SSH-tunnelled connections, and every
WebSocket reconnect wipes Streamlit's ``session_state``. The cookie carries
``name:issued:token`` where ``token`` is an HMAC of the name, the issue time and
the user's ``session_epoch`` under a server-side secret, so it cannot be forged
client-side. The server rejects a cookie older than the session lifetime, and
bumping ``session_epoch`` (password change, disabling, signing out) cancels every
cookie already handed out. It is a cookie rather than a URL parameter because
URLs leak through history, screen-sharing and proxy logs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

from . import paths
from .config import WorkspaceConfig

ROLES = ("annotator", "admin")
_ITERATIONS = 310_000


# ──────────────────────────────────────────────────────────────────────────
# Password hashing
# ──────────────────────────────────────────────────────────────────────────
def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations).hex()
    return f"pbkdf2_sha256${iterations}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt, digest = stored.split("$", 3)
    except ValueError:
        return False
    if algo != "pbkdf2_sha256":
        return False
    check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(check, digest)


# ──────────────────────────────────────────────────────────────────────────
# JSON files written atomically with private permissions
# ──────────────────────────────────────────────────────────────────────────
def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_json_atomic(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ──────────────────────────────────────────────────────────────────────────
# Identities
# ──────────────────────────────────────────────────────────────────────────
def load_users(path: Path | None = None) -> dict:
    return _read_json(path or paths.accounts_file()).get("users", {})


def save_users(users: dict, path: Path | None = None) -> None:
    write_json_atomic(path or paths.accounts_file(), {"users": users})


def add_user(name: str, password: str, role: str = "annotator", display_name: str = "",
             path: Path | None = None) -> None:
    name = name.strip()
    if not name or ":" in name or "/" in name or "__" in name:
        raise ValueError("A user id must be non-empty and contain no ':', '/' or '__'.")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if len(password) < 8:
        raise ValueError("Use a password of at least 8 characters.")
    users = load_users(path)
    if name in users:
        raise ValueError(f"User '{name}' already exists.")
    users[name] = {"password_hash": hash_password(password), "role": role,
                   "display_name": display_name or name, "active": True}
    save_users(users, path)


def set_password(name: str, password: str, path: Path | None = None) -> None:
    if len(password) < 8:
        raise ValueError("Use a password of at least 8 characters.")
    users = load_users(path)
    if name not in users:
        raise ValueError(f"No user '{name}'.")
    users[name]["password_hash"] = hash_password(password)
    users[name]["session_epoch"] = int(users[name].get("session_epoch", 0)) + 1
    save_users(users, path)


def update_user(name: str, path: Path | None = None, **fields) -> None:
    users = load_users(path)
    if name not in users:
        raise ValueError(f"No user '{name}'.")
    if "role" in fields and fields["role"] not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    was_active = users[name].get("active", True)
    users[name].update(fields)
    if was_active and not users[name].get("active", True):
        users[name]["session_epoch"] = int(users[name].get("session_epoch", 0)) + 1
    save_users(users, path)


def revoke_sessions(name: str, path: Path | None = None) -> None:
    """Cancel every sign-in cookie this person holds, on every device."""
    users = load_users(path)
    if name in users:
        users[name]["session_epoch"] = int(users[name].get("session_epoch", 0)) + 1
        save_users(users, path)


# Verified against when the user id is unknown, so a wrong id takes as long as a
# wrong password and response time does not reveal which ids exist.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def check_login(name: str, password: str, users: dict) -> bool:
    record = users.get(name)
    if not isinstance(record, dict):
        verify_password(password, _DUMMY_HASH)
        return False
    ok = verify_password(password, str(record.get("password_hash", "")))
    return ok and record.get("active", True)


# ──────────────────────────────────────────────────────────────────────────
# Sign-in throttling (in-process: the studio runs as one server process)
# ──────────────────────────────────────────────────────────────────────────
MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def _recent_failures(name: str, now: float) -> list[float]:
    recent = [t for t in _failures.get(name, []) if now - t < LOCKOUT_SECONDS]
    if recent:
        _failures[name] = recent
    else:
        _failures.pop(name, None)
    return recent


def login_locked(name: str) -> bool:
    """True after MAX_FAILURES wrong passwords for this id in the last LOCKOUT_SECONDS."""
    with _failures_lock:
        return len(_recent_failures(name, time.time())) >= MAX_FAILURES


def record_login(name: str, ok: bool) -> None:
    with _failures_lock:
        if ok:
            _failures.pop(name, None)
        else:
            _recent_failures(name, time.time())
            _failures.setdefault(name, []).append(time.time())


def session_epoch(name: str, users: dict) -> int:
    return int(users.get(name, {}).get("session_epoch", 0))


def is_admin(name: str, users: dict) -> bool:
    return users.get(name, {}).get("role", "") == "admin"


# ──────────────────────────────────────────────────────────────────────────
# Per-workspace assignments
# ──────────────────────────────────────────────────────────────────────────
def load_assignments(ws: WorkspaceConfig) -> dict:
    return _read_json(ws.assignments_file)


def save_assignments(ws: WorkspaceConfig, data: dict) -> None:
    write_json_atomic(ws.assignments_file, data, mode=0o640)


def assigned_batches(ws: WorkspaceConfig, name: str, assignments: dict | None = None) -> list[str]:
    assignments = load_assignments(ws) if assignments is None else assignments
    batches = assignments.get(name, {}).get("batches", [])
    return [str(b) for b in batches] if isinstance(batches, list) else []


def legacy_view(ws: WorkspaceConfig, users: dict, assignments: dict | None = None) -> dict:
    """``{name: {"role": ..., "batches": [...]}}`` for one workspace.

    The combined shape the query-review tasks use (role and batches in one
    record); views that need both -- e.g. the adjudicator's live peer labels --
    read this.
    """
    assignments = load_assignments(ws) if assignments is None else assignments
    return {name: {"role": users.get(name, {}).get("role", "annotator"),
                   "batches": assigned_batches(ws, name, assignments)}
            for name in assignments}


# ──────────────────────────────────────────────────────────────────────────
# Signed session tokens
# ──────────────────────────────────────────────────────────────────────────
def app_secret(path: Path | None = None) -> str:
    path = path or paths.secret_file()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
    return path.read_text(encoding="utf-8").strip()


def make_token(name: str, issued: int, epoch: int, secret: str | None = None) -> str:
    secret = secret or app_secret()
    msg = f"{name}:{issued}:{epoch}".encode()
    return hmac.new(secret.encode(), msg, hashlib.sha256).hexdigest()[:32]


def cookie_value(name: str, issued: int, users: dict, secret: str | None = None) -> str:
    return f"{name}:{issued}:{make_token(name, issued, session_epoch(name, users), secret)}"


def session_from_cookie(raw: str, users: dict, secret: str | None = None,
                        max_age: int = 30 * 24 * 3600) -> tuple[str, int] | None:
    """Return ``(user, issued)`` for a valid, unexpired cookie value, else None.

    DECODE FIRST, THEN SPLIT: the cookie is written through JS
    ``encodeURIComponent()``, which encodes the ':' separators as '%3A', so a
    check against the raw value would reject every valid cookie. ``rsplit``
    because the issue time and token can never contain ':', while a user id is
    parsed unambiguously from the right.
    """
    from urllib.parse import unquote

    if not raw:
        return None
    parts = unquote(raw).rsplit(":", 2)
    if len(parts) != 3:
        return None
    name, issued_s, token = parts
    try:
        issued = int(issued_s)
    except ValueError:
        return None
    if not 0 <= time.time() - issued <= max_age:
        return None
    record = users.get(name)
    if not record or not record.get("active", True):
        return None
    if hmac.compare_digest(token, make_token(name, issued, session_epoch(name, users), secret)):
        return name, issued
    return None


def name_from_cookie(raw: str, users: dict, secret: str | None = None,
                     max_age: int = 30 * 24 * 3600) -> str | None:
    found = session_from_cookie(raw, users, secret, max_age)
    return found[0] if found else None
