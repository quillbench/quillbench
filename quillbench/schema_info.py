"""
A fuller picture of a database for the people writing queries.

``describe`` reads, for every table: its columns and their types, primary and
foreign keys, a row estimate and any comments stored in the database. Tables that
differ only by a date suffix (``link_congestion_201904``, ``…_201905``) are folded
into one entry, so a database with hundreds of monthly tables stays readable.
Everything is read with fixed queries in a read-only session (connections.py).
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from . import connections

# Bookkeeping tables of extensions such as PostGIS; never useful for questions.
SYSTEM_TABLES = {"spatial_ref_sys", "geometry_columns", "geography_columns", "raster_columns",
                 "raster_overviews", "topology", "layer"}
_DATED = re.compile(r"^(?P<base>.+?)_(?P<stamp>\d{4}|\d{6}|\d{8})$")


@dataclass
class Column:
    name: str
    type: str
    key: str = ""          # "PK", or "→ other_table.column" for a foreign key
    comment: str = ""


@dataclass
class Table:
    name: str
    kind: str = "table"    # table, view, node, relationship
    columns: list = field(default_factory=list)
    rows: int | None = None
    comment: str = ""
    parts: list = field(default_factory=list)   # partitions, or dated tables folded into this one
    parent: bool = False                         # True: querying this name reads all the parts

    def find(self, word: str) -> bool:
        word = word.lower()
        return (word in self.name.lower() or any(word in p.lower() for p in self.parts)
                or any(word in c.name.lower() or word in c.comment.lower() for c in self.columns)
                or word in self.comment.lower())


# ──────────────────────────────────────────────────────────────────────────
# Reading each kind of database
# ──────────────────────────────────────────────────────────────────────────
_PG_COLUMNS = """
SELECT c.oid::regclass::text, a.attname, format_type(a.atttypid, a.atttypmod),
       coalesce(col_description(c.oid, a.attnum), ''), c.relkind,
       greatest(c.reltuples, 0)::bigint, coalesce(obj_description(c.oid, 'pg_class'), '')
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%'
  AND NOT c.relispartition
ORDER BY 1, a.attnum
"""
_PG_KEYS = """
SELECT k.conrelid::regclass::text, k.contype, a.attname,
       coalesce(k.confrelid::regclass::text, ''), coalesce(af.attname, '')
FROM pg_constraint k
CROSS JOIN LATERAL unnest(k.conkey, k.confkey) AS u(col, fcol)
JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.col
LEFT JOIN pg_attribute af ON af.attrelid = k.confrelid AND af.attnum = u.fcol
WHERE k.contype IN ('p', 'f')
"""
# Real (declarative) partitions: shown as parts of their parent, not as tables.
_PG_PARTS = """
SELECT i.inhparent::regclass::text, i.inhrelid::regclass::text
FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid
WHERE c.relispartition
"""
_MY_COLUMNS = """
SELECT c.table_name, c.column_name, c.column_type, c.column_comment, t.table_type,
       t.table_rows, t.table_comment, c.column_key
FROM information_schema.columns c
JOIN information_schema.tables t ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = DATABASE()
ORDER BY c.table_name, c.ordinal_position
"""
_MY_KEYS = """
SELECT table_name, column_name, referenced_table_name, referenced_column_name
FROM information_schema.key_column_usage
WHERE table_schema = DATABASE() AND referenced_table_name IS NOT NULL
"""


def _postgres(conn: connections.Connection, timeout: int) -> dict[str, Table]:
    out: dict[str, Table] = {}
    kinds = {"v": "view", "m": "view"}
    for name, col, typ, ccom, kind, rows, tcom in connections._run_unscreened(conn, _PG_COLUMNS, timeout):
        t = out.setdefault(name, Table(name, kinds.get(kind, "table"),
                                       rows=int(rows) if kind in ("r", "p", "m") else None,
                                       comment=tcom or ""))
        t.columns.append(Column(col, typ, comment=ccom or ""))
    for name, ctype, col, ref, refcol in connections._run_unscreened(conn, _PG_KEYS, timeout):
        _set_key(out, name, col, "PK" if ctype == "p" else f"→ {ref}.{refcol}")
    for parent, child in connections._run_unscreened(conn, _PG_PARTS, timeout):
        if parent in out:
            out[parent].parts.append(child)
            out[parent].parent = True
    for t in out.values():
        t.parts.sort()
    return out


def _mysql(conn: connections.Connection, timeout: int) -> dict[str, Table]:
    out: dict[str, Table] = {}
    for name, col, typ, ccom, ttype, rows, tcom, key in connections._run_unscreened(conn, _MY_COLUMNS, timeout):
        t = out.setdefault(name, Table(name, "view" if "VIEW" in str(ttype) else "table",
                                       rows=int(rows) if rows is not None else None, comment=tcom or ""))
        t.columns.append(Column(col, typ, "PK" if key == "PRI" else "", ccom or ""))
    for name, col, ref, refcol in connections._run_unscreened(conn, _MY_KEYS, timeout):
        _set_key(out, name, col, f"→ {ref}.{refcol}")
    return out


def _sqlite(conn: connections.Connection) -> dict[str, Table]:
    path = Path(conn.path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"SQLite file not found on the server: {path}")
    db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        out: dict[str, Table] = {}
        for name, kind in db.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                                     "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            t = out[name] = Table(name, kind)
            for _, col, typ, _, _, pk in db.execute("SELECT * FROM pragma_table_info(?)", (name,)):
                t.columns.append(Column(col, typ or "", "PK" if pk else ""))
            for row in db.execute("SELECT * FROM pragma_foreign_key_list(?)", (name,)):
                _set_key(out, name, row[3], f"→ {row[2]}.{row[4]}")
        return out
    finally:
        db.close()


def _neo4j(conn: connections.Connection, timeout: int) -> dict[str, Table]:
    tables, err = connections.schema(conn, timeout)
    if err:
        raise RuntimeError(err)
    out = {}
    for name, cols in tables.items():
        kind = "relationship" if name.startswith("[") else "node"
        out[name] = Table(name, kind, [Column(*(c.split(" ", 1) + [""])[:2]) for c in cols])
    return out


def _set_key(out: dict[str, Table], table: str, col: str, key: str) -> None:
    for c in out.get(table, Table(table)).columns:
        if c.name == col:
            # A column can be both: keep the primary key mark and add the reference.
            c.key = f"{c.key}, {key}" if c.key and c.key != key else key


def _fold(tables: dict[str, Table]) -> dict[str, Table]:
    """Fold dated copies (x_201904, x_201905, …) into one entry named x."""
    groups: dict[str, list[str]] = {}
    for name in tables:
        m = _DATED.match(name)
        if m:
            groups.setdefault(m.group("base"), []).append(name)
    out: dict[str, Table] = {}
    folded = set()
    for base, names in groups.items():
        if len(names) < 3 and base not in tables:
            continue
        names.sort()
        head = tables.get(base) or tables[names[-1]]
        # Listed under a name that really exists, so it can be queried as shown.
        key = base if base in tables else names[-1]
        out[key] = Table(key, head.kind, head.columns, head.rows, head.comment, names)
        folded.update(names)
        folded.add(base)
    for name, t in tables.items():
        if name not in folded:
            out[name] = t
    return dict(sorted(out.items()))


def _short(name: str) -> str:
    return name.split(".", 1)[1] if name.startswith("public.") else name


def describe(conn: connections.Connection, timeout: int = 30) -> tuple[dict[str, Table], str | None]:
    """``({name: Table}, error)``. System tables are left out; dated tables are folded."""
    try:
        if conn.engine == "postgresql":
            raw = _postgres(conn, timeout)
        elif conn.engine == "mysql":
            raw = _mysql(conn, timeout)
        elif conn.engine == "sqlite":
            raw = _sqlite(conn)
        else:
            return _neo4j(conn, timeout), None
    except Exception as exc:
        return {}, str(exc).strip() or type(exc).__name__
    raw = {k: v for k, v in raw.items() if _short(k) not in SYSTEM_TABLES and not _short(k).startswith("pg_")}
    return _fold(raw), None


def shared_columns(tables: dict[str, Table], limit: int = 40) -> list[tuple[str, list[str]]]:
    """Column names found in two or more tables: the likely ways to join them."""
    seen: dict[str, list[str]] = {}
    for name, t in tables.items():
        if t.kind in ("node", "relationship"):
            continue
        for c in t.columns:
            seen.setdefault(c.name, []).append(name)
    pairs = [(c, ts) for c, ts in seen.items() if len(ts) >= 2]
    pairs.sort(key=lambda p: (-len(p[1]), p[0]))
    return pairs[:limit]


def quote(conn: connections.Connection, name: str) -> str:
    if conn.engine == "mysql":
        return ".".join(f"`{p.replace('`', '``')}`" for p in name.split("."))
    return ".".join('"' + p.replace('"', '""') + '"' for p in name.split("."))


def sample_query(conn: connections.Connection, table: Table, n: int = 3) -> str:
    if conn.engine == "neo4j":
        if table.kind == "relationship":
            rel = table.name.strip("[]").replace("`", "``")
            return f"MATCH (a)-[r:`{rel}`]->(b) RETURN labels(a) AS from, properties(r) AS r, labels(b) AS to LIMIT {n}"
        labels = ":".join("`" + p.strip("`").replace("`", "``") + "`" for p in table.name.lstrip(":").split(":") if p)
        return f"MATCH (n:{labels}) RETURN properties(n) AS n LIMIT {n}"
    return f"SELECT * FROM {quote(conn, table.name)} LIMIT {n}"
