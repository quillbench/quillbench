"""
Sort a query into categories (join, group by, sum, …) by reading its text.

The categories are fixed here, not chosen by admins, so every project and every
database uses the same names. Admins only decide how many points each category
is worth and how many points each person must reach (see benchmark.Form).

Detection reads the query with its strings and comments blanked out, so a word
like 'join' inside a quoted value or a comment does not count.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    id: str
    label: str
    points: int          # default points; admins can change them per project
    languages: tuple     # "sql", "cypher"
    hint: str


SQL, CYPHER, BOTH = ("sql",), ("cypher",), ("sql", "cypher")

CATALOG: list[Category] = [
    # Shape of the query
    Category("simple", "Simple lookup", 1, BOTH, "One table or node type; no joins, grouping or totals."),
    Category("filter", "Filter (WHERE)", 1, BOTH, "Keeps only rows that match a condition."),
    Category("join", "Single join", 2, SQL, "Combines two tables."),
    Category("multi_join", "Multi join (3+ tables)", 4, SQL, "Combines three or more tables."),
    Category("one_hop", "One relationship", 2, CYPHER, "Follows one relationship."),
    Category("multi_hop", "Multi hop (2+ relationships)", 4, CYPHER, "Follows two or more relationships."),
    Category("var_length", "Variable-length path", 4, CYPHER, "A path of any length, e.g. -[*1..3]->."),
    Category("shortest_path", "Shortest path", 5, CYPHER, "shortestPath / allShortestPaths."),
    Category("optional", "Optional match / outer join", 3, BOTH, "LEFT/RIGHT/FULL JOIN or OPTIONAL MATCH."),
    # Totals
    Category("count", "COUNT", 1, BOTH, "Counts rows."),
    Category("sum", "SUM", 2, BOTH, "Adds values up."),
    Category("avg", "AVG", 2, BOTH, "Average of values."),
    Category("min", "MIN", 2, BOTH, "Smallest value."),
    Category("max", "MAX", 2, BOTH, "Largest value."),
    Category("collect", "COLLECT (list)", 2, CYPHER, "Gathers values into a list."),
    Category("group_by", "GROUP BY", 2, SQL, "Totals per group."),
    Category("having", "HAVING", 3, SQL, "Filters groups after totalling."),
    # Ordering and shaping
    Category("order_by", "ORDER BY (sorting)", 1, BOTH, "Sorts the result."),
    Category("top_n", "Top-N (sort + limit)", 2, BOTH, "Sorts and keeps the first N rows."),
    Category("distinct", "DISTINCT", 1, BOTH, "Removes repeated rows."),
    # Harder constructs
    Category("subquery", "Subquery", 3, BOTH, "A query inside another query."),
    Category("cte", "WITH / pipeline", 3, BOTH, "SQL common table expression, or Cypher WITH steps."),
    Category("set_operation", "UNION / INTERSECT / EXCEPT", 3, BOTH, "Combines two results."),
    Category("window", "Window function (OVER)", 4, SQL, "Running totals, ranks, moving averages."),
    Category("case", "CASE expression", 2, BOTH, "If-then logic inside the query."),
    # Kinds of condition
    Category("date_time", "Date and time", 2, BOTH, "Works with dates, hours, intervals."),
    Category("text_match", "Text matching", 1, BOTH, "LIKE, CONTAINS, STARTS WITH, regex."),
    Category("null_check", "NULL check", 1, BOTH, "IS NULL / IS NOT NULL."),
    Category("range", "Range or list (BETWEEN / IN)", 1, BOTH, "BETWEEN a AND b, or IN (…)."),
]
BY_ID = {c.id: c for c in CATALOG}


def for_language(language: str) -> list[Category]:
    return [c for c in CATALOG if language in c.languages]


def label(cid: str) -> str:
    c = BY_ID.get(cid)
    return c.label if c else cid


# ──────────────────────────────────────────────────────────────────────────
# Reading the query
# ──────────────────────────────────────────────────────────────────────────
def _mask(query: str, language: str) -> str:
    """Strings blanked, comments removed, identifiers in quotes or backticks blanked."""
    comments = r"--[^\n]*|#[^\n]*" if language == "sql" else r"//[^\n]*"
    pattern = r"'(?:[^'\\]|''|\\.)*'|\"(?:[^\"\\]|\"\"|\\.)*\"|`[^`]*`|/\*.*?\*/|" + comments

    def blank(m: re.Match) -> str:
        t = m.group(0)
        return " x " if t[0] in "'\"`" else " "

    return re.sub(pattern, blank, query, flags=re.DOTALL)


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, re.IGNORECASE) is not None


def _count(text: str, pattern: str) -> int:
    return len(re.findall(pattern, text, re.IGNORECASE))


# Functions need their "(" so a column simply named date or hour does not count.
_DATE_SQL = (r"\b(date_trunc|date_part|extract|strftime|julianday|datetime|date|time|now|"
             r"to_char|to_date|to_timestamp|timestampdiff|datediff|date_add|date_sub|dayofweek|"
             r"hour|minute|month|year|week|unix_timestamp|from_unixtime|age)\s*\(|"
             r"\b(current_date|current_timestamp|current_time|interval)\b")
# A quoted value that looks like a date or a clock time, e.g. '2024-03-01' or '08:30'.
_DATE_LITERAL = r"['\"]\d{4}-\d{2}-\d{2}|['\"]\d{1,2}:\d{2}"
_DATE_CYPHER = r"\b(date|datetime|localdatetime|time|localtime|duration|timestamp)\s*[.(]"


def _aggregates(text: str, found: set) -> None:
    for name in ("count", "sum", "avg", "min", "max"):
        if _has(text, rf"\b{name}\s*\("):
            found.add(name)


def _sql(text: str) -> set[str]:
    found: set[str] = set()
    joins = _count(text, r"\bjoin\b")
    # Old-style joins: FROM a, b  (commas between tables, before WHERE/GROUP/ORDER/…)
    for m in re.finditer(r"\bfrom\b(.*?)(?=\bwhere\b|\bgroup\b|\border\b|\blimit\b|\bhaving\b|"
                         r"\bunion\b|\)|;|$)", text, re.IGNORECASE | re.DOTALL):
        part = m.group(1)
        if not re.search(r"\(|\bjoin\b", part):
            joins += part.count(",")
    if joins == 1:
        found.add("join")
    elif joins >= 2:
        found.add("multi_join")
    if _has(text, r"\b(left|right|full)\s+(outer\s+)?join\b"):
        found.add("optional")
    if _has(text, r"\bwhere\b"):
        found.add("filter")
    _aggregates(text, found)
    if _has(text, r"\bgroup\s+by\b"):
        found.add("group_by")
    if _has(text, r"\bhaving\b"):
        found.add("having")
    # ORDER BY inside OVER (…) belongs to the window, not to the result.
    outer = re.sub(r"\bover\s*\([^)]*\)", " ", text, flags=re.IGNORECASE)
    if _has(outer, r"\border\s+by\b"):
        found.add("order_by")
        if _has(outer, r"\blimit\s+\d|\bfetch\s+(first|next)\b|\bselect\s+top\s+\d"):
            found.add("top_n")
    if _has(text, r"\bselect\s+distinct\b|\bcount\s*\(\s*distinct\b"):
        found.add("distinct")
    # "name AS (SELECT …)" is a WITH block, not a subquery.
    if _has(re.sub(r"\bas\s*\(\s*select\b", " ", text, flags=re.IGNORECASE), r"\(\s*select\b"):
        found.add("subquery")
    if _has(text, r"^\s*with\b"):
        found.add("cte")
    if _has(text, r"\b(union|intersect|except|minus)\b"):
        found.add("set_operation")
    if _has(text, r"\bover\s*\("):
        found.add("window")
    if _has(text, r"\bcase\b"):
        found.add("case")
    if _has(text, _DATE_SQL):
        found.add("date_time")
    if _has(text, r"\b(i?like|regexp|rlike|similar\s+to)\b|~\*?"):
        found.add("text_match")
    if _has(text, r"\bis\s+(not\s+)?null\b"):
        found.add("null_check")
    if _has(text, r"\bbetween\b|\bin\s*\("):
        found.add("range")
    return found


# A relationship in a pattern: -[...]-, -[...]->, <-[...]-, --, -->, <--
_REL = r"<?-\s*\[[^\]]*\]\s*->?|<?-->?"


def _cypher(text: str) -> set[str]:
    found: set[str] = set()
    rels = re.findall(_REL, text)
    if len(rels) == 1:
        found.add("one_hop")
    elif len(rels) >= 2:
        found.add("multi_hop")
    if any(re.search(r"\[[^\]]*\*", r) for r in rels):
        found.add("var_length")
    if _has(text, r"\b(all)?shortestpath\s*\("):
        found.add("shortest_path")
    if _has(text, r"\boptional\s+match\b"):
        found.add("optional")
    if _has(text, r"\bwhere\b|\bmatch\b[^\n]*\{"):
        found.add("filter")
    _aggregates(text, found)
    if _has(text, r"\bcollect\s*\("):
        found.add("collect")
    if _has(text, r"\border\s+by\b"):
        found.add("order_by")
        if _has(text, r"\blimit\s+\d"):
            found.add("top_n")
    if _has(text, r"\b(return|with)\s+distinct\b|\bcount\s*\(\s*distinct\b"):
        found.add("distinct")
    if _has(text, r"\bcall\s*\{|\b(exists|count|collect)\s*\{"):
        found.add("subquery")
    if _has(text, r"\bwith\b"):
        found.add("cte")
    if _has(text, r"\bunion\b"):
        found.add("set_operation")
    if _has(text, r"\bcase\b"):
        found.add("case")
    if _has(text, _DATE_CYPHER):
        found.add("date_time")
    if _has(text, r"\b(contains|starts\s+with|ends\s+with)\b|=~"):
        found.add("text_match")
    if _has(text, r"\bis\s+(not\s+)?null\b"):
        found.add("null_check")
    if _has(text, r"\bin\s*\[|\bin\s+\$"):
        found.add("range")
    return found


# Categories that make a query more than a simple lookup.
_NOT_SIMPLE = {"join", "multi_join", "one_hop", "multi_hop", "var_length", "shortest_path",
               "optional", "count", "sum", "avg", "min", "max", "collect", "group_by", "having",
               "subquery", "cte", "set_operation", "window"}


def detect(query: str, language: str = "sql") -> list[str]:
    """The categories a query falls into, in catalogue order. Never empty for a real query."""
    if not str(query or "").strip():
        return []
    language = "cypher" if language == "cypher" else "sql"
    text = _mask(query, language)
    found = _cypher(text) if language == "cypher" else _sql(text)
    if re.search(_DATE_LITERAL, query):
        found.add("date_time")
    if not found & _NOT_SIMPLE:
        found.add("simple")
    return [c.id for c in CATALOG if c.id in found]


# Complexity of a query: fixed by the system from its hardest category, so it does not
# change when admins change the points.
LEVELS = {1: "Easy", 2: "Medium", 3: "Hard"}
TOP_LEVEL = "Very hard"
LEVEL_ORDER = ["Easy", "Medium", "Hard", "Very hard"]


def complexity(cats: list[str]) -> str:
    if not cats:
        return ""
    hardest = max(BY_ID[c].points for c in cats if c in BY_ID)
    return LEVELS.get(hardest, TOP_LEVEL)


def guess_language(query: str) -> str:
    return "cypher" if re.match(r"\s*(optional\s+)?match\b|\s*call\b", query or "", re.IGNORECASE) else "sql"
