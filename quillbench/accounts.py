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
  workspace (``quillbench_assignments.json``), so they travel with the paper's data and
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

import csv
import fcntl
import functools
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import ldap_auth, paths
from .config import WorkspaceConfig

ROLES = ("annotator", "admin")

# Admins are made only on the server, by someone who can run the ``quillbench``
# command as the account that owns the studio (``quillbench user add NAME --admin``
# or ``quillbench user role NAME admin``). The web pages never grant or remove the
# role, and an account that signs in with a university (LDAP) login is never an
# admin, whatever accounts.json says.
NO_LDAP_ADMIN = "University (LDAP) accounts cannot be admins."


def is_ldap_account(record: dict) -> bool:
    return record.get("source") == "ldap" and not record.get("password_hash")
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


_held = threading.local()


@contextmanager
def accounts_lock():
    """One change to accounts.json at a time, across threads and processes.

    Each change reads the whole file, edits one record and writes it back; two at
    once (say a person signing out while an admin adds a student) would otherwise
    lose one of them. Re-entrant within a thread.
    """
    if getattr(_held, "depth", 0):
        _held.depth += 1
        try:
            yield
        finally:
            _held.depth -= 1
        return
    lock = paths.accounts_file().with_name(".accounts.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        _held.depth = 1
        try:
            yield
        finally:
            _held.depth = 0
            fcntl.flock(handle, fcntl.LOCK_UN)


def _serialised(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with accounts_lock():
            return fn(*args, **kwargs)
    return wrapper


# ──────────────────────────────────────────────────────────────────────────
# Identities
# ──────────────────────────────────────────────────────────────────────────
def load_users(path: Path | None = None) -> dict:
    return _read_json(path or paths.accounts_file()).get("users", {})


def save_users(users: dict, path: Path | None = None) -> None:
    write_json_atomic(path or paths.accounts_file(), {"users": users})


@_serialised
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


@_serialised
def add_ldap_user(name: str, role: str = "annotator", display_name: str = "",
                  path: Path | None = None) -> None:
    """An account that signs in with its university (LDAP) password; none is stored here."""
    name = name.strip()
    if not ldap_auth.USER_RE.match(name):
        raise ValueError("A university user id may use only letters, digits, '.', '_' and '-'.")
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if role == "admin":
        raise ValueError(NO_LDAP_ADMIN)
    users = load_users(path)
    if name in users:
        raise ValueError(f"User '{name}' already exists.")
    users[name] = {"role": role, "display_name": display_name or name, "active": True, "source": "ldap"}
    save_users(users, path)


@_serialised
def set_password(name: str, password: str, path: Path | None = None) -> None:
    if len(password) < 8:
        raise ValueError("Use a password of at least 8 characters.")
    users = load_users(path)
    if name not in users:
        raise ValueError(f"No user '{name}'.")
    users[name]["password_hash"] = hash_password(password)
    users[name]["session_epoch"] = int(users[name].get("session_epoch", 0)) + 1
    save_users(users, path)


@_serialised
def update_user(name: str, path: Path | None = None, **fields) -> None:
    users = load_users(path)
    if name not in users:
        raise ValueError(f"No user '{name}'.")
    if "role" in fields and fields["role"] not in ROLES:
        raise ValueError(f"role must be one of {ROLES}")
    if fields.get("role") == "admin" and is_ldap_account(users[name]):
        raise ValueError(NO_LDAP_ADMIN)
    was_active = users[name].get("active", True)
    users[name].update(fields)
    if was_active and not users[name].get("active", True):
        users[name]["session_epoch"] = int(users[name].get("session_epoch", 0)) + 1
    save_users(users, path)


@_serialised
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


def sign_in(name: str, password: str, users: dict, ldap: dict | None = None) -> bool:
    """Check a sign-in against QuillBench's own accounts, then LDAP when it is set up.

    An account with its own password always uses it, so LDAP can never take over a
    local account. A person LDAP accepts for the first time gets an annotator
    account (no password stored) when ``auto_create`` is on, or must already have
    been added by an admin.
    """
    record = users.get(name)
    if not ldap_auth.enabled(ldap) or (isinstance(record, dict) and record.get("password_hash")):
        return check_login(name, password, users)
    if isinstance(record, dict) and not record.get("active", True):
        return False
    if record is None and not ldap.get("auto_create", True):
        verify_password(password, _DUMMY_HASH)
        return False
    if not ldap_auth.check(name, password, ldap):
        return False
    if record is None:
        with accounts_lock():
            users = load_users()
            if name not in users:
                users[name] = {"role": "annotator", "display_name": name, "active": True, "source": "ldap"}
                save_users(users)
    return True


# ──────────────────────────────────────────────────────────────────────────
# Sign-in throttling (in-process: the studio runs as one server process)
# ──────────────────────────────────────────────────────────────────────────
# Three limits over the last LOCKOUT_SECONDS:
#  * one user id from one address: MAX_FAILURES wrong passwords;
#  * one user id from anywhere: MAX_FAILURES_PER_ID, so guessing from many machines
#    stops too, and a university account is never hammered into its own lockout;
#  * one address: MAX_FAILURES_PER_IP, whatever ids it tries.
# A classmate typing someone else's id from their own machine blocks only
# themselves until the per-id limit; the owner can still sign in from theirs.
MAX_FAILURES = 5
MAX_FAILURES_PER_ID = 15
MAX_FAILURES_PER_IP = 40
LOCKOUT_SECONDS = 15 * 60
_MAX_KEYS = 20_000
_failures: dict[tuple, list[float]] = {}
_failures_lock = threading.Lock()


def _recent(key: tuple, now: float) -> list[float]:
    recent = [t for t in _failures.get(key, []) if now - t < LOCKOUT_SECONDS]
    if recent:
        _failures[key] = recent
    else:
        _failures.pop(key, None)
    return recent


def login_locked(name: str, ip: str = "") -> bool:
    """True while this id (from this address), this id overall, or this address is blocked."""
    now = time.time()
    with _failures_lock:
        return (len(_recent(("id-ip", name, ip), now)) >= MAX_FAILURES
                or len(_recent(("id", name), now)) >= MAX_FAILURES_PER_ID
                or (bool(ip) and len(_recent(("ip", ip), now)) >= MAX_FAILURES_PER_IP))


def record_login(name: str, ok: bool, client: dict | None = None) -> None:
    client = client or {}
    ip = client.get("ip", "")
    now = time.time()
    with _failures_lock:
        if ok:
            _failures.pop(("id-ip", name, ip), None)
        else:
            if len(_failures) > _MAX_KEYS:        # random ids must not grow memory forever
                for key in [k for k in list(_failures) if not _recent(k, now)]:
                    _failures.pop(key, None)
            for key in (("id-ip", name, ip), ("id", name)) + ((("ip", ip),) if ip else ()):
                _recent(key, now)
                _failures.setdefault(key, []).append(now)
    log_signin(name, "ok" if ok else "wrong password", client)


_log_lock = threading.Lock()


def log_signin(name: str, result: str, client: dict | None = None) -> None:
    """One line per sign-in attempt in ``logs/signins.csv`` (mode 600) in the studio home."""
    client = client or {}
    path = paths.accounts_file().parent / "logs" / "signins.csv"
    row = [datetime.now(timezone.utc).isoformat(timespec="seconds"), str(name)[:64], result,
           client.get("ip", ""), client.get("user_agent", "")[:300]]
    try:
        with _log_lock:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            new = not path.exists()
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                if new:
                    w.writerow(["time_utc", "user", "result", "ip", "user_agent"])
                w.writerow(row)
    except OSError:
        pass        # a full disk must not stop people signing in


def session_epoch(name: str, users: dict) -> int:
    return int(users.get(name, {}).get("session_epoch", 0))


def owner(users: dict) -> str:
    """The first admin account (accounts are stored in the order they were made).

    Only this person may make or remove admins in the web page; everyone can still be
    changed from the server with ``quillbench user role``.
    """
    return next((n for n, r in users.items() if r.get("role") == "admin" and not is_ldap_account(r)), "")


@_serialised
def set_admin(actor: str, name: str, make: bool, path: Path | None = None) -> None:
    """Make ``name`` an admin, or an annotator again, on behalf of ``actor`` (must be the owner)."""
    users = load_users(path)
    if actor != owner(users) or not users.get(actor, {}).get("active", True):
        raise ValueError("Only the first admin can change who is an admin here.")
    if name == actor:
        raise ValueError("You cannot change your own role here; use the server for that.")
    if name not in users:
        raise ValueError(f"No user '{name}'.")
    update_user(name, path, role="admin" if make else "annotator")
    revoke_sessions(name, path)      # the new role applies from their next sign-in


def is_admin(name: str, users: dict) -> bool:
    record = users.get(name, {})
    return record.get("role", "") == "admin" and not is_ldap_account(record)


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
