"""
Database connections that admins set up for benchmark-creation projects.

Connection details, passwords included, live only in the studio's private folder
(``connections.json``, readable by the studio's own account only). A project's
folder and its CSV files hold just the connection's id, and the people writing
benchmark items see only its name.

Every query runs read-only, in two independent layers:

1. The database session itself is read-only (SQLite opens the file read-only,
   PostgreSQL and MySQL start a READ ONLY transaction, Neo4j uses READ_ACCESS),
   with a time limit.
2. Before it is sent, the query is screened: one statement, starting with
   SELECT or WITH (SQL) and no write keywords; Cypher uses the screen in
   ``graphdb.py``.

The screen fails closed (a write keyword inside a string is refused too). The
strongest protection is still a database account that can only read; use one.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .accounts import _read_json, write_json_atomic

ENGINES = {
    "postgresql": "PostgreSQL",
    "mysql": "MySQL / MariaDB",
    "sqlite": "SQLite (file on this server)",
    "neo4j": "Neo4j (Cypher)",
}
DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306, "neo4j": 7687}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


@dataclass(frozen=True)
class Connection:
    id: str
    name: str
    engine: str
    host: str = ""
    port: int = 0
    database: str = ""
    user: str = ""
    password: str = ""
    path: str = ""          # SQLite only

    @property
    def language(self) -> str:
        return "cypher" if self.engine == "neo4j" else "sql"

    @property
    def where(self) -> str:
        """Where it points, without the password, for the admin page."""
        if self.engine == "sqlite":
            return self.path
        db = f"/{self.database}" if self.database else ""
        return f"{self.user}@{self.host}:{self.port}{db}"


@dataclass(frozen=True)
class QueryResult:
    columns: list
    rows: list              # list of lists, already plain values
    elapsed: float
    error: str | None = None
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None


# ──────────────────────────────────────────────────────────────────────────
# Finding out what is listening on a host and port
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Detected:
    engine: str | None      # one of ENGINES, or None when it is not a database we support
    message: str


def split_address(text: str, port: int = 0) -> tuple[str, int]:
    """``db.example.org:5433`` or ``[::1]:5432`` -> host and port; a bare host keeps ``port``."""
    text = text.strip()
    for scheme in ("bolt://", "neo4j://", "neo4j+s://", "bolt+s://", "postgresql://",
                   "postgres://", "mysql://"):
        if text.lower().startswith(scheme):
            text = text[len(scheme):]
    text = text.split("/", 1)[0].split("@")[-1]
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        return host, int(rest[1:]) if rest.startswith(":") and rest[1:].isdigit() else port
    if text.count(":") == 1:
        host, _, p = text.partition(":")
        if p.isdigit():
            return host, int(p)
    return text, port


def _recv(sock, n: int, wait: float) -> bytes:
    import socket

    sock.settimeout(wait)
    try:
        return sock.recv(n)
    except (socket.timeout, OSError):
        return b""


def _open(host: str, port: int, timeout: float):
    import socket

    return socket.create_connection((host, port), timeout=timeout)


def _banner(data: bytes) -> Detected | None:
    """The server spoke first: MySQL and MariaDB do, and so do SSH, mail and FTP."""
    if len(data) > 5 and data[4] == 10:              # MySQL protocol 10 greeting
        version = data[5:].split(b"\0", 1)[0].decode("ascii", "replace")
        name = "MariaDB" if "mariadb" in version.lower() else "MySQL"
        return Detected("mysql", f"{name} {version.replace('-MariaDB', '').split('-')[0]}".strip())
    if len(data) > 5 and data[4] == 0xFF:            # MySQL error packet, e.g. host not allowed
        text = data[7:].decode("utf-8", "replace").lstrip("#").strip()
        return Detected("mysql", f"MySQL or MariaDB, but it refused this server: {text[:150]}")
    text = data[:80].decode("utf-8", "replace").strip()
    if text.startswith("SSH-"):
        return Detected(None, f"An SSH server ({text.splitlines()[0]}), not a database.")
    if text[:3].isdigit():
        return Detected(None, f"A mail or file server ('{text.splitlines()[0][:60]}'), not a database.")
    return None


def detect(host: str, port: int, timeout: float = 5.0) -> Detected:
    """What database answers on ``host:port``, found without logging in.

    Each database starts a connection differently: MySQL and MariaDB greet first;
    PostgreSQL answers its 8-byte SSL request with a single 'S' or 'N'; Neo4j's Bolt
    port answers its 4-byte magic number with the protocol version it picked.
    """
    import socket
    import struct

    host, port = split_address(host, port)
    if not host or not 0 < port < 65536:
        return Detected(None, "Enter a host and a port.")
    try:
        sock = _open(host, port, timeout)
    except socket.gaierror:
        return Detected(None, f"The name '{host}' could not be found. Check the host.")
    except (socket.timeout, TimeoutError):
        return Detected(None, f"No answer from {host}:{port} within {timeout:.0f} s. "
                              "A firewall may be blocking this server.")
    except OSError as exc:
        return Detected(None, f"Could not reach {host}:{port}: {exc.strerror or exc}.")
    with sock:
        first = _recv(sock, 256, 1.5)
        if first:
            return _banner(first) or Detected(None, "Something answers on this port, but not a "
                                                    "database QuillBench knows.")
        try:
            sock.sendall(struct.pack("!II", 8, 80877103))     # PostgreSQL SSLRequest
        except OSError:
            pass
        reply = _recv(sock, 64, 2.0)
    if reply[:1] in (b"S", b"N") and len(reply) == 1:
        return Detected("postgresql", "PostgreSQL")
    if reply.startswith(b"E") and b"SFATAL" in reply:      # PostgreSQL turned the request down
        return Detected("postgresql", "PostgreSQL")
    if reply.startswith(b"HTTP/"):
        return _http(host, port, timeout)
    try:
        with _open(host, port, timeout) as sock:
            sock.sendall(bytes.fromhex("6060B017") + bytes.fromhex("00080805 00020404 00000104 00000003"))
            reply = _recv(sock, 4, 2.0)
    except OSError:
        reply = b""
    if len(reply) == 4 and reply != b"\0\0\0\0":
        return Detected("neo4j", f"Neo4j (Bolt {reply[3]}.{reply[2]})")
    if len(reply) == 4:
        return Detected("neo4j", "Neo4j, but it supports none of the Bolt versions the driver speaks")
    if reply.startswith(b"HTTP/"):
        return _http(host, port, timeout)
    return _http(host, port, timeout) if port in (80, 443, 7474, 7473, 8080) else Detected(
        None, "Something answers on this port, but it did not reply like PostgreSQL, MySQL, "
              "MariaDB or Neo4j. SQL Server and Oracle are not supported.")


def _http(host: str, port: int, timeout: float) -> Detected:
    try:
        with _open(host, port, timeout) as sock:
            sock.sendall(f"GET / HTTP/1.0\r\nHost: {host}\r\n\r\n".encode())
            body = _recv(sock, 4096, 2.0).decode("utf-8", "replace").lower()
    except OSError:
        body = ""
    if "neo4j" in body or "bolt_routing" in body or "bolt_direct" in body:
        return Detected(None, "This is Neo4j's web port. Use its Bolt port instead (usually 7687).")
    if body.startswith("http/"):
        return Detected(None, "A web server answers on this port, not a database.")
    return Detected(None, "Something answers on this port, but not a database QuillBench knows.")


# ──────────────────────────────────────────────────────────────────────────
# Storage
# ──────────────────────────────────────────────────────────────────────────
def connections_file() -> Path:
    return paths.studio_home() / "connections.json"


def load_all() -> dict[str, Connection]:
    raw = _read_json(connections_file()).get("connections", {})
    out = {}
    for cid, r in raw.items():
        fields = {k: r[k] for k in Connection.__dataclass_fields__ if k in r and k != "id"}
        out[cid] = Connection(id=cid, **fields)
    return out


def get(cid: str) -> Connection | None:
    return load_all().get(cid)


def save(conn: Connection) -> None:
    """Add or replace one connection. An empty password keeps the stored one."""
    if not _ID_RE.match(conn.id):
        raise ValueError("A connection id uses lower-case letters, digits, '_' and '-' (up to 40).")
    if conn.engine not in ENGINES:
        raise ValueError(f"Unknown engine '{conn.engine}'.")
    if not conn.name.strip():
        raise ValueError("Give the connection a name.")
    if conn.engine == "sqlite":
        if not conn.path.strip():
            raise ValueError("Give the path of the SQLite file on the server.")
    elif not (conn.host.strip() and conn.user.strip()):
        raise ValueError("Host and user are required.")
    data = _read_json(connections_file())
    allc = data.setdefault("connections", {})
    record = {k: getattr(conn, k) for k in Connection.__dataclass_fields__ if k != "id"}
    if not conn.password and conn.id in allc:
        record["password"] = allc[conn.id].get("password", "")
    allc[conn.id] = record
    write_json_atomic(connections_file(), data)


def delete(cid: str) -> None:
    data = _read_json(connections_file())
    data.get("connections", {}).pop(cid, None)
    write_json_atomic(connections_file(), data)


# ──────────────────────────────────────────────────────────────────────────
# The read-only screen for SQL
# ──────────────────────────────────────────────────────────────────────────
_SQL_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|REPLACE(?!\s*\()|CREATE|ALTER|DROP|TRUNCATE|RENAME|GRANT|"
    r"REVOKE|ATTACH|DETACH|PRAGMA|COPY|CALL|EXEC|EXECUTE|DO|LOAD|VACUUM|REINDEX|ANALYZE|"
    r"LOCK|UNLOCK|SET|RESET|INTO|OUTFILE|DUMPFILE|HANDLER|PREPARE|DEALLOCATE|LISTEN|NOTIFY|"
    r"COMMIT|ROLLBACK|BEGIN|START|SAVEPOINT|SHUTDOWN|KILL|FLUSH|INSTALL|UNINSTALL)\b",
    re.IGNORECASE,
)
# Functions that read or write server files, sleep, or open other connections.
_SQL_FUNCTION_RE = re.compile(
    r"\b(pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|"
    r"pg_sleep|dblink\w*|load_file|sleep|benchmark|sys_exec|sys_eval|readfile|writefile|"
    r"load_extension|fts3_tokenizer)\s*\(",
    re.IGNORECASE,
)


def _mask_sql(query: str, mysql: bool = False) -> str:
    """The query with string contents blanked and comments turned into spaces.

    Strings and comments are found in one left-to-right pass, so a comment marker
    inside a string, or a quote inside a comment, cannot hide anything from the
    checks below. What is inside a string can never run, so it is not checked.
    """
    pattern = (r"'(?:[^'\\]|''|\\.)*'|\"(?:[^\"]|\"\")*\"|`[^`]*`|--[^\n]*|/\*.*?\*/"
               + (r"|#[^\n]*" if mysql else ""))

    def blank(m: re.Match) -> str:
        t = m.group(0)
        return t[0] * 2 if t[0] in "'\"`" else " "

    masked = re.sub(pattern, blank, query, flags=re.DOTALL)
    # An unclosed quote or comment: keep the rest, so it is still checked.
    return masked


def sql_guard_reason(query: str, mysql: bool = False) -> str | None:
    """Why this SQL is refused, or None if it may run."""
    cleaned = _mask_sql(query, mysql).strip().rstrip(";").strip()
    if not cleaned:
        return "the query is empty"
    if ";" in cleaned:
        return "only one statement can run at a time"
    first = cleaned.split(None, 1)[0].upper().lstrip("(")
    if first not in ("SELECT", "WITH"):
        return "only read queries (SELECT or WITH) can run"
    match = _SQL_WRITE_RE.search(cleaned)
    if match:
        return f"contains '{match.group(1).upper()}', which can change data or settings"
    match = _SQL_FUNCTION_RE.search(cleaned)
    if match:
        return f"uses '{match.group(1)}', which is not allowed here"
    return None


def guard_reason(conn: Connection, query: str) -> str | None:
    if conn.engine == "neo4j":
        from .graphdb import write_guard_reason

        if not query.strip():
            return "the query is empty"
        return write_guard_reason(query)
    return sql_guard_reason(query, mysql=conn.engine == "mysql")


# ──────────────────────────────────────────────────────────────────────────
# Running a query
# ──────────────────────────────────────────────────────────────────────────
def _plain(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<{len(bytes(value))} bytes>"
    return str(value)


def _sqlite(conn: Connection, query: str, timeout: int, cap: int):
    path = Path(conn.path).expanduser()
    if not path.is_file():
        raise RuntimeError(f"SQLite file not found on the server: {path}")
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    deadline = time.monotonic() + timeout
    db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
    try:
        cur = db.execute(query)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(cap + 1)
    finally:
        db.close()
    return cols, rows


def _postgres(conn: Connection, query: str, timeout: int, cap: int):
    try:
        import psycopg
    except ImportError:
        raise RuntimeError("PostgreSQL support is not installed on the server: "
                           "pip install 'quillbench[postgres]'") from None
    with psycopg.connect(host=conn.host, port=conn.port or 5432, dbname=conn.database or None,
                         user=conn.user, password=conn.password, connect_timeout=10,
                         options=f"-c statement_timeout={int(timeout * 1000)} "
                                 f"-c default_transaction_read_only=on") as db:
        db.read_only = True
        with db.cursor() as cur:
            cur.execute(query)
            cols = [d.name for d in cur.description or []]
            rows = cur.fetchmany(cap + 1) if cur.description else []
        db.rollback()
    return cols, rows


def _mysql(conn: Connection, query: str, timeout: int, cap: int):
    try:
        import pymysql
    except ImportError:
        raise RuntimeError("MySQL support is not installed on the server: "
                           "pip install 'quillbench[mysql]'") from None
    db = pymysql.connect(host=conn.host, port=conn.port or 3306, user=conn.user,
                         password=conn.password, database=conn.database or None,
                         connect_timeout=10, read_timeout=timeout + 5, autocommit=False)
    try:
        with db.cursor() as cur:
            try:
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME={int(timeout * 1000)}")
            except Exception:  # MariaDB names it differently
                cur.execute(f"SET SESSION max_statement_time={int(timeout)}")
            cur.execute("START TRANSACTION READ ONLY")
            cur.execute(query)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchmany(cap + 1) if cur.description else []
        db.rollback()
    finally:
        db.close()
    return cols, rows


def _neo4j(conn: Connection, query: str, timeout: int, cap: int):
    from neo4j import READ_ACCESS, GraphDatabase

    uri = conn.host if "://" in conn.host else f"bolt://{conn.host}:{conn.port or 7687}"
    with GraphDatabase.driver(uri, auth=(conn.user, conn.password)) as driver:
        kwargs = {"default_access_mode": READ_ACCESS}
        if conn.database:
            kwargs["database"] = conn.database
        with driver.session(**kwargs) as session:
            with session.begin_transaction(timeout=float(timeout)) as tx:
                result = tx.run(query)
                cols = list(result.keys())
                rows = []
                for rec in result:
                    rows.append([rec[c] for c in cols])
                    if len(rows) > cap:
                        break
                tx.rollback()
    return cols, rows


_RUNNERS = {"sqlite": _sqlite, "postgresql": _postgres, "mysql": _mysql, "neo4j": _neo4j}


def run(conn: Connection, query: str, timeout: int = 30, cap: int = 200) -> QueryResult:
    """Run one read query. Never raises: problems come back as ``error``."""
    started = time.perf_counter()
    reason = guard_reason(conn, query)
    if reason:
        return QueryResult([], [], 0.0, f"Not run: {reason}.")
    try:
        cols, rows = _RUNNERS[conn.engine](conn, query, timeout, cap)
    except Exception as exc:  # driver, auth, syntax, timeout: reported as-is
        msg = str(exc).strip() or type(exc).__name__
        if isinstance(exc, sqlite3.OperationalError) and "interrupted" in msg:
            msg = f"The query took longer than {timeout} s and was stopped."
        return QueryResult([], [], round(time.perf_counter() - started, 3), msg)
    rows = [[_plain(v) for v in r] for r in rows]
    return QueryResult(list(cols), rows[:cap], round(time.perf_counter() - started, 3),
                       truncated=len(rows) > cap)


# ──────────────────────────────────────────────────────────────────────────
# Schema, for the schema slot
# ──────────────────────────────────────────────────────────────────────────
_SCHEMA_SQL = {
    "postgresql": ("SELECT table_schema || '.' || table_name, column_name, data_type "
                   "FROM information_schema.columns WHERE table_schema NOT IN "
                   "('pg_catalog', 'information_schema') "
                   "ORDER BY table_schema, table_name, ordinal_position"),
    "mysql": ("SELECT table_name, column_name, column_type FROM information_schema.columns "
              "WHERE table_schema = DATABASE() ORDER BY table_name, ordinal_position"),
}


def schema(conn: Connection, timeout: int = 30) -> tuple[dict[str, list[str]], str | None]:
    """``{table or label: ["column type", ...]}`` read from the database itself."""
    try:
        if conn.engine == "sqlite":
            path = Path(conn.path).expanduser()
            if not path.is_file():
                return {}, f"SQLite file not found on the server: {path}"
            db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
            try:
                out = {}
                tables = [r[0] for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
                for t in tables:
                    cols = db.execute("SELECT name, type FROM pragma_table_info(?)", (t,)).fetchall()
                    out[t] = [f"{c} {ty}".strip() for c, ty in cols]
                return out, None
            finally:
                db.close()
        if conn.engine == "neo4j":
            out: dict[str, list[str]] = {}
            res = _run_unscreened(conn, "CALL db.schema.nodeTypeProperties() YIELD nodeType, "
                                        "propertyName, propertyTypes RETURN nodeType, propertyName, "
                                        "propertyTypes", timeout)
            for node, prop, types in res:
                out.setdefault(str(node), [])
                if prop:
                    out[str(node)].append(f"{prop} {'/'.join(types or [])}".strip())
            res = _run_unscreened(conn, "CALL db.schema.relTypeProperties() YIELD relType, "
                                        "propertyName, propertyTypes RETURN relType, propertyName, "
                                        "propertyTypes", timeout)
            for rel, prop, types in res:
                key = f"[{str(rel).lstrip(':').strip('`')}]"
                out.setdefault(key, [])
                if prop:
                    out[key].append(f"{prop} {'/'.join(types or [])}".strip())
            return out, None
        rows = _run_unscreened(conn, _SCHEMA_SQL[conn.engine], timeout)
        out = {}
        for table, column, kind in rows:
            out.setdefault(str(table), []).append(f"{column} {kind}")
        return out, None
    except Exception as exc:
        return {}, str(exc).strip() or type(exc).__name__


def _run_unscreened(conn: Connection, query: str, timeout: int) -> list:
    """Fixed schema queries written here, never user text; still read-only sessions."""
    _, rows = _RUNNERS[conn.engine](conn, query, timeout, 100_000)
    return rows


def test(conn: Connection) -> str | None:
    """None if a trivial read query works, else the error."""
    probe = "RETURN 1 AS ok" if conn.engine == "neo4j" else "SELECT 1"
    res = run(conn, probe, timeout=10, cap=1)
    return res.error
