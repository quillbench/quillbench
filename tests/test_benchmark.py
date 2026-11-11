"""Benchmark creation: connections, the read-only screen, duplicates, ids, admin rules."""

import csv
import os
import sqlite3
import stat
import threading
import time

import pytest
from streamlit.testing.v1 import AppTest

from quillbench import accounts, benchmark, builder, connections, registry
from quillbench.connections import sql_guard_reason

from test_app import APP, _login


@pytest.fixture
def city_db(tmp_path):
    path = tmp_path / "city.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE station (id INTEGER PRIMARY KEY, name TEXT, city TEXT);
        CREATE TABLE reading (station_id INTEGER, day TEXT, pm25 REAL);
        INSERT INTO station VALUES (1, 'North', 'Osaka'), (2, 'Bay', 'Kobe');
        INSERT INTO reading VALUES (1, '2026-01-01', 12.5), (2, '2026-01-01', 30.1);
    """)
    db.commit()
    db.close()
    return path


@pytest.fixture
def conn(studio, city_db):
    connections.save(connections.Connection("city", "City air", "sqlite", path=str(city_db)))
    return connections.get("city")


@pytest.fixture
def project(conn):
    accounts.add_user("boss", "password1", "admin")
    accounts.add_user("alice", "password1", "annotator")
    accounts.add_user("bob", "password1", "annotator")
    return builder.create_benchmark_project("Air questions", "city", "boss",
                                            participants=["alice", "bob"])


# ── the read-only screen ──────────────────────────────────────────────────
@pytest.mark.parametrize("query", [
    "UPDATE station SET name='x'", "DELETE FROM station", "DROP TABLE station",
    "SELECT 1; DROP TABLE station", "select a from t where x='#'; DROP TABLE t",
    "SELECT * INTO copy FROM station", "PRAGMA writable_schema=1", "ATTACH 'x.db' AS x",
    "SELECT pg_sleep(100)", "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x", "",
])
def test_writes_are_refused(query):
    assert sql_guard_reason(query, mysql=True) is not None


@pytest.mark.parametrize("query", [
    "SELECT * FROM station", "select name from station where city = 'Osaka';",
    "WITH s AS (SELECT * FROM station) SELECT count(*) FROM s",
    "SELECT REPLACE(name, 'N', 'n') FROM station", "SELECT 'update; drop' AS words",
    "SELECT * FROM station -- a comment; DROP", "SELECT * FROM t LIMIT 5 OFFSET 5",
])
def test_reads_are_allowed(query):
    assert sql_guard_reason(query) is None


def test_sqlite_runs_read_only(conn, city_db):
    res = connections.run(conn, "SELECT name FROM station ORDER BY id")
    assert res.ok and res.columns == ["name"] and res.rows == [["North"], ["Bay"]]
    assert not connections.run(conn, "DELETE FROM station").ok
    # Even past the screen, the file is opened read-only.
    with pytest.raises(RuntimeError):
        try:
            connections._sqlite(conn, "INSERT INTO station VALUES (3, 'x', 'y')", 5, 10)
        except sqlite3.OperationalError as exc:
            raise RuntimeError(exc)
    assert len(sqlite3.connect(city_db).execute("SELECT * FROM station").fetchall()) == 2


def test_errors_and_row_cap(conn):
    assert "no such table" in connections.run(conn, "SELECT * FROM missing_table").error
    res = connections.run(conn, "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n "
                                "WHERE i < 50) SELECT i FROM n", cap=10)
    assert len(res.rows) == 10 and res.truncated


def test_slow_query_is_stopped(conn):
    slow = ("WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i+1 FROM n) "
            "SELECT count(*) FROM n")
    started = time.time()
    res = connections.run(conn, slow, timeout=1)
    assert not res.ok and "longer than 1 s" in res.error and time.time() - started < 10


def test_schema_is_read_from_the_database(conn):
    tables, err = connections.schema(conn)
    assert err is None and tables["station"] == ["id INTEGER", "name TEXT", "city TEXT"]


def test_connection_file_is_private_and_password_kept(studio):
    connections.save(connections.Connection("pg", "Main", "postgresql", host="db", port=5432,
                                            user="reader", password="s3cret"))
    path = connections.connections_file()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    connections.save(connections.Connection("pg", "Main renamed", "postgresql", host="db",
                                            port=5432, user="reader", password=""))
    assert connections.get("pg").password == "s3cret" and connections.get("pg").name == "Main renamed"
    assert "s3cret" not in connections.get("pg").where


# ── similarity, ids and the two files ─────────────────────────────────────
def test_similarity_ignores_case_and_punctuation():
    assert benchmark.similarity("How many stations are in Osaka?", "how many STATIONS are in osaka") == 1.0
    assert benchmark.similarity("How many stations are in Osaka?", "Which day had the worst PM2.5?") < 0.5


def _add(ws, who, question, query="SELECT * FROM station", **answers):
    form = benchmark.load_form(ws)
    return benchmark.submit(ws, form, who, question, query, ["id"], [[1]], answers)


def test_duplicates_go_to_rejected_with_score_and_match(project):
    first = _add(project, "alice", "How many stations are in Osaka?")
    assert first.accepted and first.id.startswith("q_")
    again = _add(project, "bob", "How many stations are there in Osaka?")
    assert not again.accepted and again.matched_id == first.id and again.similarity >= 0.8
    other = _add(project, "bob", "What was the PM2.5 reading at Bay station on New Year's Day?")
    assert other.accepted
    accepted, rejected = benchmark.read_accepted(project), benchmark.read_rejected(project)
    assert [r["id"] for r in accepted] == [first.id, other.id]
    assert rejected[0]["matched_id"] == first.id and float(rejected[0]["similarity"]) >= 0.8
    assert rejected[0]["author"] == "bob" and rejected[0]["id"] == again.id
    assert benchmark.counts(project) == {"alice": {"added": 1, "not_added": 0},
                                         "bob": {"added": 1, "not_added": 1}}


def test_threshold_is_configurable(project):
    form = benchmark.load_form(project)
    form.threshold = 1.0
    benchmark.save_form(project, form)
    assert _add(project, "alice", "How many stations are in Osaka?").accepted
    assert _add(project, "alice", "How many stations are there in Osaka?").accepted
    assert not _add(project, "alice", "how many stations are in osaka").accepted


def test_many_people_adding_at_once(project):
    outcomes = []

    def add(i):
        outcomes.append(_add(project, f"p{i % 3}", f"Question number {i} about {'abcdefghijklmnopqrst'[i] * 30}"))

    threads = [threading.Thread(target=add, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    rows = benchmark.read_accepted(project)
    assert len(rows) == 20 and len({r["id"] for r in rows}) == 20


def test_new_slot_adds_a_column_without_losing_rows(project):
    _add(project, "alice", "How many stations are in Osaka?")
    form = benchmark.load_form(project)
    form.slots.append(benchmark.Slot("difficulty", "choice", "Difficulty", options=["easy", "hard"]))
    benchmark.save_form(project, form)
    _add(project, "bob", "Which city has the Bay station?", difficulty="easy")
    with benchmark.accepted_path(project).open() as f:
        rows = list(csv.DictReader(f))
    assert [r["difficulty"] for r in rows] == ["", "easy"]


def test_form_checks():
    form = benchmark.Form(connection="c", slots=benchmark.default_slots())
    assert benchmark.check_form(form) == []
    form.slots = [s for s in form.slots if s.type != "result"]
    assert any("result" in p.lower() for p in benchmark.check_form(form))
    form = benchmark.Form(connection="c", slots=benchmark.default_slots()
                          + [benchmark.Slot("author", "short_text")])
    assert any("reserved" in p for p in benchmark.check_form(form))


# ── admin rules ───────────────────────────────────────────────────────────
def test_ldap_accounts_are_never_admins(studio):
    with pytest.raises(ValueError, match="cannot be admins"):
        accounts.add_ldap_user("uni1", "admin")
    accounts.add_ldap_user("uni1")
    with pytest.raises(ValueError, match="cannot be admins"):
        accounts.update_user("uni1", role="admin")
    users = accounts.load_users()
    users["uni1"]["role"] = "admin"          # even if the file is edited by hand
    assert not accounts.is_admin("uni1", users)
    accounts.add_user("local", "password1", "admin")
    assert accounts.is_admin("local", accounts.load_users())


def test_people_tab_cannot_make_admins(project):
    at = _login(AppTest.from_file(APP), "boss")
    assert not [s for s in at.selectbox if s.label == "Role"]


# ── the page people use ───────────────────────────────────────────────────
def _field(at, label):
    return next(t for t in at.text_area if t.label == label)


def _btn(at, label):
    return next(b for b in at.button if b.label == label)


def test_participant_adds_an_entry_end_to_end(project):
    registry.add(project.root)
    at = _login(AppTest.from_file(APP), "alice")
    assert any(t.label == "Create" for t in at.tabs)
    assert _btn(at, "Add to benchmark").disabled
    _field(at, "Question").input("Which city is the North station in?")
    _field(at, "SQL query").input("SELECT city FROM station WHERE name = 'North'")
    at.run(timeout=30)
    _btn(at, "Run query").click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    assert _btn(at, "Add to benchmark").disabled          # the check box is not ticked yet
    next(c for c in at.checkbox if "answers my question" in c.label).check()
    at.run(timeout=30)
    assert not _btn(at, "Add to benchmark").disabled
    _btn(at, "Add to benchmark").click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    rows = benchmark.read_accepted(project)
    assert len(rows) == 1 and rows[0]["author"] == "alice" and rows[0]["row_count"] == "1"
    assert next(m for m in at.metric if m.label == "Added by you").value == "1"
    assert any("Added to the benchmark" in s.value for s in at.success)


def test_editing_the_query_after_running_locks_add_again(project):
    registry.add(project.root)
    at = _login(AppTest.from_file(APP), "bob")
    _field(at, "Question").input("List every station")
    _field(at, "SQL query").input("SELECT name FROM station")
    at.run(timeout=30)
    _btn(at, "Run query").click()
    at.run(timeout=30)
    next(c for c in at.checkbox if "answers my question" in c.label).check()
    _field(at, "SQL query").input("SELECT name FROM station WHERE 1 = 0")
    at.run(timeout=30)
    assert _btn(at, "Add to benchmark").disabled
    assert any("run the query" in c.value for c in at.caption)


def test_non_participants_do_not_see_the_project(project):
    registry.add(project.root)
    accounts.add_user("carol", "password1", "annotator")
    at = _login(AppTest.from_file(APP), "carol")
    assert any("Nothing is assigned to you" in i.value for i in at.info)
