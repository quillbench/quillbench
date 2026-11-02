"""
Graph-database access for the Query Console.

Two hard rules live here:

1. Every session opens in READ access mode.
2. Every query is screened by :func:`is_write_query` before it is sent.

Rule 2 is not redundant with rule 1. READ_ACCESS is enforced by the server
and depends on the deployment being configured for it; the regex screen is a
client-side guard that gives the annotator an immediate, comprehensible
rejection instead of a driver error, and protects deployments where the
account genuinely has write rights.
"""

from __future__ import annotations

import re
import time
from typing import Any

import streamlit as st
from neo4j import READ_ACCESS, GraphDatabase

from .config import GraphDBConfig

# ──────────────────────────────────────────────────────────────────────────
# The write guard
#
# WHAT IS BLOCKED, AND WHY
#
# 1. Cypher write clauses (`_WRITE_CLAUSE_RE`): CREATE, MERGE, SET, DELETE,
#    DETACH, REMOVE, DROP, FOREACH, LOAD CSV. Unchanged from the original.
#
# 2. Every `CALL dbms.*` procedure. These are administrative:
#    dbms.security.createUser, dbms.setConfigValue and dbms.killQueries
#    mutate users, server configuration and running sessions respectively.
#    A few (dbms.components) are harmless reads, but the family is small,
#    almost never needed for benchmark exploration, and the cost asymmetry is
#    stark: one mildly confused user versus an attacker-created superuser.
#    Blocked wholesale, deliberately.
#
# 3. Named `apoc.*` families that write, execute, or perform I/O:
#    create, merge, refactor, atomic, trigger, periodic, schema, export,
#    import, load, custom, systemdb, cypher, uuid, static, bolt, es, mongo,
#    couchbase, redis, dv, generate, model, nlp.
#    Two of these deserve calling out: `apoc.export.*` needs no database
#    write mode at all — it writes files to *server* disk — and
#    `apoc.cypher.*` (doIt / runWrite / runMany) evaluates a query string,
#    i.e. it is an arbitrary-code-execution sink.
#    This is deliberately NOT a blanket `CALL apoc.` block: the read-only
#    helpers that make an exploration console genuinely useful —
#    apoc.meta.*, apoc.text.*, apoc.convert.*, apoc.coll.*, apoc.map.*,
#    apoc.path.*, apoc.algo.*, apoc.date.*, apoc.number.* — stay available.
#
# 4. Any procedure whose *name* contains a mutating verb
#    (`_MUTATING_PROC_VERB_RE`). This is the general net behind the
#    enumerated lists, and it is what keeps administrative `db.*` procedures
#    blocked: db.createLabel, db.createProperty, db.createRelationshipType,
#    db.index.fulltext.createNodeIndex, db.clearQueryCaches, db.resampleIndex.
#
# WHAT IS DELIBERATELY NO LONGER BLOCKED
#
#    `CALL db.` used to be blocked wholesale. That rejected genuinely
#    read-only schema introspection — db.labels(), db.relationshipTypes(),
#    db.propertyKeys(), db.schema.visualization(), db.indexes(), db.info() —
#    which is among the most useful things to run in an exploration console.
#    Rule 4 keeps the administrative `db.*` procedures blocked without that
#    collateral cost.
#
# HONEST LIMITATIONS — this is a regex, not a Cypher parser
#
#  * String-literal obfuscation is NOT closed. Splitting a keyword across a
#    concatenation ("CRE" + "ATE (n:X)") is invisible to any keyword regex.
#    `apoc.cypher.doIt("CRE"+"ATE ...")` is caught here only because the
#    *procedure name* is on the blocklist — not because the payload was
#    understood. A different eval sink, or a procedure not on these lists,
#    would slip past. Closing this class properly requires a real parser or
#    server-side enforcement. Do not describe this guard as complete.
#  * Conversely, a keyword inside a string literal is treated as a write
#    (`... WHERE n.note = 'we should CREATE more'` is rejected). That false
#    positive is accepted on purpose: the guard fails closed.
#  * Consequently this guard is the SECOND line of defence, not the only
#    one. The first is READ_ACCESS on every session (see run_query), which
#    the server enforces. The strongest remains a genuinely read-only
#    database account — the only layer that does not depend on this file
#    being exhaustive.
# ──────────────────────────────────────────────────────────────────────────
_WRITE_CLAUSE_RE = re.compile(
    r"\b(CREATE|MERGE|SET|DELETE|DETACH|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b",
    re.IGNORECASE,
)

_BLOCKED_APOC_FAMILIES = (
    "create", "merge", "refactor", "atomic", "trigger", "periodic", "schema",
    "export", "import", "load", "custom", "systemdb", "cypher", "uuid",
    "static", "bolt", "es", "mongo", "couchbase", "redis", "dv", "generate",
    "model", "nlp",
)

_BLOCKED_PROC_RE = re.compile(
    r"\bCALL\s+(?:dbms\.|apoc\.(?:" + "|".join(_BLOCKED_APOC_FAMILIES) + r")\b)",
    re.IGNORECASE,
)

_MUTATING_PROC_VERB_RE = re.compile(
    r"\bCALL\s+[\w.]*\b(?:create|delete|drop|remove|rename|install|add|set|"
    r"update|write|import|export|kill|terminate|clear|restore|resample|"
    r"upsert|truncate)",
    re.IGNORECASE,
)


def strip_comments(query: str) -> str:
    """Replace // line comments and /* block */ comments with a space.

    Done before the write screen so a mutation cannot be smuggled past it by
    hiding the keyword inside something the screen would otherwise skip.
    """
    query = re.sub(r"//[^\n]*", " ", query)
    return re.sub(r"/\*.*?\*/", " ", query, flags=re.DOTALL)


def _normalise(query: str) -> str:
    """The text the screen runs on.

    Comments become a space, not nothing: the server reads a comment as
    whitespace, so ``LOAD/**/CSV`` *is* ``LOAD CSV`` to Neo4j, and deleting the
    comment would hide it as ``LOADCSV``. Backticks are dropped so a quoted name
    (``CALL `apoc`.`export`...``) reads as the plain dotted name, and runs of
    whitespace (including around dots) are collapsed.
    """
    cleaned = strip_comments(query).replace("`", "")
    cleaned = re.sub(r"\s*\.\s*", ".", cleaned)
    return re.sub(r"\s+", " ", cleaned)


def write_guard_reason(query: str) -> str | None:
    """Why this query is rejected, or None if it may run. See the block
    comment above for the full policy and its documented limitations."""
    cleaned = _normalise(query)
    match = _WRITE_CLAUSE_RE.search(cleaned)
    if match:
        return f"contains the write clause '{match.group(1).upper()}'"
    match = _BLOCKED_PROC_RE.search(cleaned)
    if match:
        return (
            f"calls '{match.group(0).split(None, 1)[1].strip()}…', an "
            f"administrative or write/IO-capable procedure family"
        )
    match = _MUTATING_PROC_VERB_RE.search(cleaned)
    if match:
        return f"calls a mutating procedure ('{match.group(0).split(None, 1)[1].strip()}')"
    return None


def is_write_query(query: str) -> bool:
    return write_guard_reason(query) is not None


@st.cache_resource(show_spinner=False)
def _get_driver(uri: str, user: str, password: str, _pool: int = 10):
    return GraphDatabase.driver(uri, auth=(user, password), max_connection_pool_size=_pool)


def run_query(
    cypher: str, target: GraphDBConfig, timeout: int = 30
) -> tuple[list[dict[str, Any]], float, str | None]:
    """Execute a read query. Returns ``(records, elapsed_seconds, error)``."""
    driver = _get_driver(target.uri, target.user, target.password)
    started = time.perf_counter()
    session_kwargs: dict[str, Any] = {"default_access_mode": READ_ACCESS}
    if target.database:
        session_kwargs["database"] = target.database
    try:
        with driver.session(**session_kwargs) as session:
            with session.begin_transaction(timeout=float(timeout)) as tx:
                records = [dict(r) for r in tx.run(cypher)]
                tx.commit()
        return records, round(time.perf_counter() - started, 3), None
    except Exception as exc:  # driver, auth, syntax, timeout — all reported as-is
        return [], round(time.perf_counter() - started, 3), str(exc)
