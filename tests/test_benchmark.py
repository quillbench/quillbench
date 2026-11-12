"""Benchmark creation: connections, the read-only screen, duplicates, ids, admin rules."""

import csv
import os
import sqlite3
import stat
import threading
import time

import pytest
import yaml
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


def _verdict(at):
    return next(r for r in at.radio if "Correct" in r.options)


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
    assert any("Complexity: Easy" in m.value for m in at.markdown)
    assert not any("Filter (WHERE)" in m.value for m in at.markdown)   # categories are for admins
    assert not any("Correct" in r.options for r in at.radio)           # no verdict asked by default
    assert not _btn(at, "Add to benchmark").disabled
    _btn(at, "Add to benchmark").click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    rows = benchmark.read_accepted(project)
    assert len(rows) == 1 and rows[0]["author"] == "alice" and rows[0]["row_count"] == "1"
    assert next(m for m in at.metric if m.label == "Added by you").value == "1"
    assert any("Added as" in t.value for t in at.toast)
    assert rows[0]["verdict"] == "" and rows[0]["categories"] == "simple;filter"
    assert [t.label for t in at.tabs][:4] == ["Create", "Schema", "Progress", "Profile"]


def test_editing_the_query_after_running_locks_add_again(project):
    registry.add(project.root)
    at = _login(AppTest.from_file(APP), "bob")
    _field(at, "Question").input("List every station")
    _field(at, "SQL query").input("SELECT name FROM station")
    at.run(timeout=30)
    _btn(at, "Run query").click()
    at.run(timeout=30)
    _field(at, "SQL query").input("SELECT name FROM station WHERE 1 = 0")
    at.run(timeout=30)
    assert _btn(at, "Add to benchmark").disabled
    assert any("run the query" in c.value for c in at.caption)


def test_non_participants_do_not_see_the_project(project):
    registry.add(project.root)
    accounts.add_user("carol", "password1", "annotator")
    at = _login(AppTest.from_file(APP), "carol")
    assert any("Nothing is assigned to you" in i.value for i in at.info)


# ── categories, points, verdicts ──────────────────────────────────────────
def test_points_follow_categories_and_verdicts(project):
    form = benchmark.load_form(project)
    form.slot("result").ask_verdict = True
    form.points = {"join": 5}
    form.targets = {"join": 10, "group_by": 2}
    form.min_total = 6
    benchmark.save_form(project, form)
    form = benchmark.load_form(project)
    assert form.points_for("join") == 5 and form.points_for("filter") == 1
    q = "SELECT s.city FROM station s JOIN line l ON l.id = s.line WHERE l.name = 'A'"
    benchmark.submit(project, form, "alice", "Cities on line A", q, ["city"], [["x"]], verdict="Correct")
    benchmark.submit(project, form, "alice", "Something quite different here", q, ["city"], [["x"]],
                     verdict="Incorrect")
    p = benchmark.profiles(project, form)["alice"]
    assert p.added == 2 and p.total == 5                  # the best category only (join 5); Incorrect earns nothing
    assert p.per_category["join"] == {"entries": 2, "points": 5}
    assert p.entries[0]["_level"] == "Medium" and p.entries[0]["_top"] == "join"
    assert form.score(["filter", "join", "avg"]) == (5, "join")
    assert p.missing(form) == {"join": 5, "group_by": 2}
    assert not p.done(form)
    assert p.verdicts == {"Correct": 1, "Incorrect": 1}


def test_old_entries_and_old_forms_still_work(project):
    """Rows from before 1.9 have no verdict or categories; forms have the old tick box text."""
    form = benchmark.load_form(project)
    raw = yaml.safe_load(benchmark.form_path(project).read_text())
    for s in raw["slots"]:
        if s["type"] == "result":
            s["confirm"] = "I checked the result, and it answers my question."
            s.pop("verdicts", None)
    benchmark.form_path(project).write_text(yaml.safe_dump(raw))
    form = benchmark.load_form(project)
    assert form.slot("result").verdicts == ["Correct", "Incorrect", "Uncertain"]
    assert "I checked" not in form.slot("result").confirm
    benchmark.data_dir(project).mkdir(parents=True, exist_ok=True)
    benchmark.accepted_path(project).write_text(
        "id,author,created_at,question,query,row_count,result_columns,result_preview\n"
        "q_1,bob,2026-01-01,How many?,SELECT COUNT(*) FROM station,1,[],[]\n")
    p = benchmark.profiles(project, form)["bob"]
    assert p.total == 1 and "count" in p.per_category       # counted as Correct
    benchmark.submit(project, form, "bob", "Totally new question", "SELECT name FROM station",
                     ["name"], [["x"]], verdict="Uncertain")
    rows = benchmark.read_accepted(project)
    assert rows[0]["id"] == "q_1" and rows[1]["verdict"] == "Uncertain"


def test_form_rejects_bad_verdict_settings(project):
    form = benchmark.load_form(project)
    form.slot("result").ask_verdict = True
    form.slot("result").accept = ["Nope"]
    assert any("verdict must allow" in p for p in benchmark.check_form(form))


def test_similarity_is_kept_for_added_entries_and_nearest_ranks(project):
    form = benchmark.load_form(project)
    q = "SELECT name FROM station"
    benchmark.submit(project, form, "alice", "Which holidays were there in May 2019?", q, ["n"], [["x"]], verdict="Correct")
    out = benchmark.submit(project, form, "bob", "Which national holidays were in December 2019?", q,
                           ["n"], [["x"]], verdict="Correct")
    assert out.accepted and 0.6 < out.similarity < 0.8
    rows = benchmark.read_accepted(project)
    assert rows[1]["matched_id"] == rows[0]["id"] and float(rows[1]["similarity"]) == round(out.similarity, 3)
    best = benchmark.nearest("What holidays were there in May 2019?", rows, 1)
    assert best[0][1]["id"] == rows[0]["id"] and best[0][0] > 0.9


def test_check_similarity_button(project):
    registry.add(project.root)
    form = benchmark.load_form(project)
    benchmark.submit(project, form, "bob", "List every station", "SELECT name FROM station", ["n"], [["x"]],
                     verdict="Correct")
    at = _login(AppTest.from_file(APP), "alice")
    _field(at, "Question").input("List every station please")
    at.run(timeout=30)
    _btn(at, "Check similarity").click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    assert any("would not be added" in t.value and "already in the benchmark" in t.value for t in at.toast)
    assert not any("every station”" in t.value for t in at.toast)    # a classmate's text is not shown


def test_verdict_buttons_when_the_admin_turns_them_on(project):
    form = benchmark.load_form(project)
    form.slot("result").ask_verdict = True
    benchmark.save_form(project, form)
    registry.add(project.root)
    at = _login(AppTest.from_file(APP), "alice")
    _field(at, "Question").input("Which city is the North station in?")
    _field(at, "SQL query").input("SELECT city FROM station WHERE name = 'North'")
    at.run(timeout=30)
    _btn(at, "Run query").click()
    at.run(timeout=30)
    assert _btn(at, "Add to benchmark").disabled
    _verdict(at).set_value("Uncertain")
    at.run(timeout=30)
    _btn(at, "Add to benchmark").click()
    at.run(timeout=30)
    assert benchmark.read_accepted(project)[0]["verdict"] == "Uncertain"
    p = benchmark.profiles(project)["alice"]
    assert p.total == 0                                    # only Correct earns points


def test_schema_tab_shows_overview_and_one_table(project):
    registry.add(project.root)
    at = _login(AppTest.from_file(APP), "alice")
    assert any("station(" in c.value for c in at.code)       # copyable overview, in a collapsible
    assert any("All tables at a glance" in e.label for e in at.expander)
    pick = next(s for s in at.selectbox if s.label == "Choose a table")
    pick.set_value("station")
    at.run(timeout=30)
    assert not at.exception, at.exception
    assert any("First 10 rows" in m.value for m in at.markdown)
