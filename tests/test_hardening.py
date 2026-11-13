"""Per-person files, sign-in limits, the shared query limit and safe account writes."""

import csv
import os
import stat
import threading
import time

import pytest

from quillbench import accounts, benchmark, connections, ldap_auth
from test_benchmark import city_db, conn, project  # noqa: F401  (fixtures)

CLIENT = {"ip": "10.0.0.7", "user_agent": "Mozilla/5.0 Test", "language": "ja", "session": "abc"}


def _submit(ws, who, question, client=CLIENT):
    form = benchmark.load_form(ws)
    return benchmark.submit(ws, form, who, question, "SELECT name FROM station",
                            ["name"], [["North"]], client=client)


def test_every_attempt_goes_to_the_persons_own_file(project):
    ws = project
    assert _submit(ws, "alice", "Which stations are in Osaka?").accepted
    assert not _submit(ws, "alice", "Which stations are in Osaka?").accepted   # repeat
    _submit(ws, "bob", "How many readings are there per day in total?")
    rows = benchmark.read_student(ws, "alice")
    assert [r["status"] for r in rows] == ["added", "not added (too similar)"]
    first = rows[0]
    assert first["ip"] == "10.0.0.7" and first["user_agent"] == "Mozilla/5.0 Test"
    assert first["browser_language"] == "ja" and first["session"] == "abc"
    assert first["submitted_at"] and first["submitted_at_utc"].endswith("+00:00")
    assert first["question"] == "Which stations are in Osaka?" and first["query"]
    assert len(benchmark.read_student(ws, "bob")) == 1
    path = benchmark.student_path(ws, "alice")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    # The shared file keeps no addresses.
    assert "ip" not in benchmark.read_accepted(ws)[0]


def test_student_file_names_are_safe(project):
    path = benchmark.student_path(project, "../../etc/passwd")
    assert path.parent == benchmark.students_dir(project)
    assert "/" not in path.name and not path.name.startswith(".")


def test_timezone_setting(project, studio):
    (studio / "settings.yaml").write_text("timezone: Asia/Tokyo\n")
    _submit(project, "alice", "Which stations are in Kobe?")
    assert benchmark.read_student(project, "alice")[0]["submitted_at"].endswith("+09:00")


def test_parallel_adds_lose_nothing(project):
    errors = []

    def add(i):
        try:
            _submit(project, "alice" if i % 2 else "bob", f"Question number {i} about zebra {i * 7919}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=add, args=(i,)) for i in range(40)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    total = len(benchmark.read_accepted(project)) + len(benchmark.read_rejected(project))
    assert total == 40
    assert len(benchmark.read_student(project, "alice")) + len(benchmark.read_student(project, "bob")) == 40
    assert len({r["id"] for r in benchmark.read_accepted(project)}) == len(benchmark.read_accepted(project))


def test_long_result_cells_are_clipped(project):
    form = benchmark.load_form(project)
    benchmark.submit(project, form, "alice", "A very wide answer?", "SELECT 1", ["x"], [["y" * 10_000]])
    assert len(benchmark.read_accepted(project)[0]["result_preview"]) < 1_000


# ── sign-in limits ────────────────────────────────────────────────────────
@pytest.fixture(autouse=True)
def _clear_failures():
    accounts._failures.clear()
    yield
    accounts._failures.clear()


def test_a_classmate_cannot_lock_someone_out_from_their_own_machine(studio):
    for _ in range(accounts.MAX_FAILURES):
        accounts.record_login("s1", False, {"ip": "10.0.0.66"})
    assert accounts.login_locked("s1", "10.0.0.66")
    assert not accounts.login_locked("s1", "10.0.0.5")       # the owner's machine


def test_guessing_one_id_from_many_machines_stops(studio):
    for i in range(accounts.MAX_FAILURES_PER_ID):
        accounts.record_login("s1", False, {"ip": f"10.0.1.{i}"})
    assert accounts.login_locked("s1", "10.0.9.9")


def test_one_machine_trying_many_ids_stops(studio):
    for i in range(accounts.MAX_FAILURES_PER_IP):
        accounts.record_login(f"s{i}", False, {"ip": "10.0.0.66"})
    assert accounts.login_locked("someone_else", "10.0.0.66")
    assert not accounts.login_locked("someone_else", "10.0.0.5")


def test_sign_ins_are_logged_privately(studio):
    accounts.add_user("alice", "password1")
    accounts.record_login("alice", True, CLIENT)
    accounts.record_login("alice", False, CLIENT)
    path = studio / "logs" / "signins.csv"
    rows = list(csv.DictReader(path.open()))
    assert [r["result"] for r in rows] == ["ok", "wrong password"]
    assert rows[0]["ip"] == "10.0.0.7"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


# ── accounts.json under load ──────────────────────────────────────────────
def test_parallel_account_changes_are_all_kept(studio):
    for i in range(30):
        accounts.add_user(f"u{i}", "password1")
    threads = [threading.Thread(target=accounts.revoke_sessions, args=(f"u{i}",)) for i in range(30)]
    threads += [threading.Thread(target=accounts.add_user, args=(f"new{i}", "password1")) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    users = accounts.load_users()
    assert all(users[f"u{i}"].get("session_epoch") == 1 for i in range(30))
    assert all(f"new{i}" in users for i in range(10))


def test_set_admin_still_works_with_the_lock(studio):
    accounts.add_user("boss", "password1", "admin")
    accounts.add_user("al", "password1")
    accounts.set_admin("boss", "al", True)
    assert accounts.is_admin("al", accounts.load_users())


# ── shared query limit ────────────────────────────────────────────────────
def test_one_query_per_person_and_a_shared_limit(conn, monkeypatch):
    gate = threading.Event()

    def slow(c, q, timeout=30, cap=200):
        gate.wait(5)
        return connections.QueryResult(["x"], [[1]], 0.0)

    monkeypatch.setattr(connections, "run", slow)
    monkeypatch.setattr(connections, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(connections, "QUEUE_WAIT", 0.2)
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("a", connections.run_shared(conn, "SELECT 1", "alice")))
    t.start()
    time.sleep(0.1)
    again = connections.run_shared(conn, "SELECT 1", "alice")
    assert "still running" in again.error
    queued = connections.run_shared(conn, "SELECT 1", "bob")
    assert "busy" in queued.error
    gate.set()
    t.join()
    assert out["a"].ok
    assert connections.run_shared(conn, "SELECT 1", "alice").ok      # free again


# ── LDAP ──────────────────────────────────────────────────────────────────
def test_plain_ldap_without_tls_is_refused():
    assert not ldap_auth.check("s1", "pw", {"url": "ldap://example.edu", "user_dn": "uid={user},dc=x"})


def test_students_zip_is_safe_for_spreadsheets(project):
    import io
    import zipfile

    _submit(project, "alice", "=HYPERLINK(\"http://evil\",\"x\") which stations?")
    z = zipfile.ZipFile(io.BytesIO(benchmark.students_zip(project)))
    assert z.namelist() == ["alice.csv"]
    text = z.read("alice.csv").decode("utf-8-sig")
    assert "'=HYPERLINK" in text and "\n=HYPERLINK" not in text
