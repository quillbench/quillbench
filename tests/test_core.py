import json
import time

import pandas as pd
import pytest

from quillbench import builder, accounts, registry
from quillbench.config import ConfigError, load_workspace
from quillbench.quality import cohen_kappa, krippendorff_alpha, report
from quillbench.storage import (batch_status, get_progress_df, labeled_mask, list_batches, load_batch,
                         progress_path, progress_summary_rows, set_verdict)
from quillbench.validate import validate_workspace


# ── accounts ─────────────────────────────────────────────────────────────
def test_password_hash_roundtrip(studio):
    accounts.add_user("alice", "correct horse", "annotator")
    users = accounts.load_users()
    assert "correct horse" not in json.dumps(users)
    assert accounts.check_login("alice", "correct horse", users)
    assert not accounts.check_login("alice", "wrong", users)
    accounts.update_user("alice", active=False)
    assert not accounts.check_login("alice", "correct horse", accounts.load_users())


def test_accounts_file_is_private(studio):
    accounts.add_user("bob", "password1")
    assert oct(accounts.paths.accounts_file().stat().st_mode)[-3:] == "600"


def test_cookie_token(studio):
    accounts.add_user("lab_alice", "password1")
    users = accounts.load_users()
    now = int(time.time())
    value = accounts.cookie_value("lab_alice", now, users)
    assert accounts.name_from_cookie(value.replace(":", "%3A"), users) == "lab_alice"
    assert accounts.name_from_cookie(f"lab_alice:{now}:{'0' * 32}", users) is None
    assert accounts.name_from_cookie("", users) is None
    # Expired on the server side, whatever the browser kept.
    old = now - 31 * 24 * 3600
    assert accounts.name_from_cookie(accounts.cookie_value("lab_alice", old, users), users) is None
    # A password change cancels cookies already handed out.
    accounts.set_password("lab_alice", "another long one")
    assert accounts.name_from_cookie(value, accounts.load_users()) is None


def test_login_throttle():
    for _ in range(accounts.MAX_FAILURES):
        accounts.record_login("mallory", False)
    assert accounts.login_locked("mallory")
    accounts.record_login("mallory", True)
    assert not accounts.login_locked("mallory")


def test_write_guard_sees_through_comments_and_backticks():
    from quillbench.graphdb import write_guard_reason

    assert write_guard_reason("LOAD/**/CSV FROM 'file:///etc/passwd' AS r RETURN r")
    assert write_guard_reason("CALL/**/apoc.export.csv.all('x', {})")
    assert write_guard_reason("CALL `apoc`.`export`.csv.all('x', {})")
    assert write_guard_reason("MATCH (n) DETACH/**/DELETE n")
    assert write_guard_reason("CALL db.labels()") is None


def test_bad_user_ids(studio):
    for bad in ("", "a:b", "a/b", "a__b"):
        with pytest.raises(ValueError):
            accounts.add_user(bad, "password1")
    with pytest.raises(ValueError):
        accounts.add_user("short", "pw")


# ── config ───────────────────────────────────────────────────────────────
def test_routing(workspace):
    ws = load_workspace(workspace)
    assert ws.project_for_batch("rate_01").id == "demo_rating"
    assert ws.project_for_batch("pick_01").id == "demo_choice"
    assert ws.project_for_batch("anything").id == "demo_verdict"
    assert ws.project_by_id("demo_choice").task_spec.values == ("YES", "NO", "MAYBE")
    assert ws.project_by_id("demo_rating").task_spec.rating.unsure_label == "Can't judge"


def test_two_defaults_rejected(workspace):
    text = (workspace / "quillbench_workspace.yaml").read_text().replace("task: rating", "task: rating\n    default: true")
    (workspace / "quillbench_workspace.yaml").write_text(text)
    with pytest.raises(ConfigError, match="more than one"):
        load_workspace(workspace)


def test_unknown_task_rejected(workspace):
    text = (workspace / "quillbench_workspace.yaml").read_text().replace("task: choice", "task: vibes")
    (workspace / "quillbench_workspace.yaml").write_text(text)
    with pytest.raises(ConfigError, match="task must be one of"):
        load_workspace(workspace)


def test_validate_clean(workspace):
    errors, warnings = validate_workspace(load_workspace(workspace))
    assert errors == []


# ── storage ──────────────────────────────────────────────────────────────
def test_rating_answers_and_progress(workspace):
    ws = load_workspace(workspace)
    assert set(list_batches(ws)) == {"rate_01", "pick_01", "b_01"}
    batch = load_batch(ws, "rate_01")
    set_verdict(ws, "alice", "rate_01", "q1", "0.85", "", batch, seconds=12.34)
    set_verdict(ws, "alice", "rate_01", "q2", "UNSURE", "unclear", batch, seconds=3)
    df = get_progress_df(ws, "alice", "rate_01", batch)
    assert labeled_mask(df, ws.project_by_id("demo_rating")).tolist() == [True, True, False]
    saved = pd.read_csv(progress_path(ws, "alice", "rate_01"))
    assert saved.loc[0, "seconds"] == 12.3 and saved.loc[0, "updated_at"].endswith("Z")
    assert batch_status(ws, "alice", "rate_01") == {"total": 3, "labeled": 2, "pct": 67}
    # no temp files left behind
    assert not list((workspace / "progress").glob(".*tmp"))


def test_jsonl_batch_and_out_of_range(workspace):
    ws = load_workspace(workspace)
    batch = load_batch(ws, "pick_01")
    assert list(batch["id"]) == ["p0", "p1", "p2"]
    set_verdict(ws, "bob", "pick_01", "p0", "YES", "", batch)
    set_verdict(ws, "bob", "pick_01", "p1", "CORRECT", "", batch)   # not a label of this task
    assert batch_status(ws, "bob", "pick_01")["labeled"] == 1


def test_also_accept_counts_earlier_labels(workspace):
    ws = load_workspace(workspace)
    batch = load_batch(ws, "b_01")
    set_verdict(ws, "carol", "b_01", 1, "ACCEPT", "", batch)
    assert batch_status(ws, "carol", "b_01")["labeled"] == 1
    rows, problems = progress_summary_rows(ws)
    assert problems == [] and rows[0]["Labeled"] == 1


def test_legacy_progress_file_reads_unchanged(workspace):
    """A progress file with no timing columns."""
    ws = load_workspace(workspace)
    (workspace / "progress").mkdir()
    (workspace / "progress" / "dave__b_01.csv").write_text(
        "id,nl_query,gold_cypher,human_verdict,note,paraphrase\n1,how many?,MATCH,CORRECT,,\n")
    assert batch_status(ws, "dave", "b_01")["labeled"] == 1


# ── quality ──────────────────────────────────────────────────────────────
def test_krippendorff_reference_values():
    # Krippendorff (2011), "Computing Krippendorff's alpha-reliability", worked example.
    d = {1: [1, 1, 1], 2: [2, 2, 3, 2], 3: [3, 3, 3, 3], 4: [3, 3, 3, 3], 5: [2, 2, 2, 2],
         6: [1, 2, 3, 4], 7: [4, 4, 4, 4], 8: [1, 1, 2, 1], 9: [2, 2, 2, 2], 10: [5, 5, 5],
         11: [1, 1], 12: [3]}
    assert round(krippendorff_alpha(d, "nominal"), 3) == 0.743
    assert round(krippendorff_alpha(d, "ordinal"), 3) == 0.815
    assert round(krippendorff_alpha(d, "interval"), 3) == 0.849


def test_cohen_kappa():
    assert round(cohen_kappa(list("aabbab"), list("abbbab")), 3) == 0.667
    assert cohen_kappa(["a"], ["a"]) is None


def test_quality_report_with_checks(workspace):
    ws = load_workspace(workspace)
    batch = load_batch(ws, "rate_01")
    for who, vals in (("alice", ("0.8", "0.1", "0.95")), ("bob", ("0.9", "0.2", "0.5"))):
        for qid, v in zip(("q1", "q2", "c1"), vals):
            set_verdict(ws, who, "rate_01", qid, v, "", batch, seconds=5)
    rep = report(ws, ws.project_by_id("demo_rating"))
    assert rep["items_with_2plus"] == 2          # the check item is left out
    assert rep["alpha"] is not None and rep["alpha"] > 0.8
    checks = {p["Annotator"]: p["Checks passed"] for p in rep["people"]}
    assert checks == {"alice": "1/1", "bob": "0/1"}


# ── registry / export ────────────────────────────────────────────────────
def test_registry_and_export(workspace, studio):
    registry.add(workspace)
    loaded, problems = registry.load_all()
    assert [w.id for w in loaded] == ["demo"] and problems == []
    ws = loaded[0]
    batch = load_batch(ws, "pick_01")
    set_verdict(ws, "alice", "pick_01", "p0", "YES", "a note", batch)
    set_verdict(ws, "bob", "pick_01", "p0", "NO", "", batch)
    from quillbench.export import export

    out = export(ws)
    data = pd.read_csv(out / "answers.csv")
    assert set(data["annotator"]) == {"A01", "A02"}
    assert "alice" not in (out / "answers.csv").read_text()
    assert (out / "DATASHEET.md").exists()
    assert json.loads((studio / "annotator_codes.json").read_text()) == {"alice": "A01", "bob": "A02"}


def test_broken_manifest_does_not_hide_others(workspace, tmp_path, studio):
    registry.add(workspace)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "quillbench_workspace.yaml").write_text("workspace: {id: bad}\nprojects: []\n")
    reg = registry.manifest_paths() + [bad / "quillbench_workspace.yaml"]
    registry._write(reg)
    loaded, problems = registry.load_all()
    assert [w.id for w in loaded] == ["demo"] and len(problems) == 1


def test_unquoted_no_label_explained(workspace):
    text = (workspace / "quillbench_workspace.yaml").read_text().replace('"NO"', "NO")
    (workspace / "quillbench_workspace.yaml").write_text(text)
    with pytest.raises(ConfigError, match="must be quoted"):
        load_workspace(workspace)


def test_first_admin_only_once(studio):
    accounts.create_first_admin("first", "password1")
    assert accounts.is_admin("first", accounts.load_users())
    with pytest.raises(ValueError):
        accounts.create_first_admin("second", "password1")
    assert "second" not in accounts.load_users()


def _table(n=10):
    return pd.DataFrame({"text": [f"item {i}" for i in range(n)]})


def test_project_from_interface(studio):
    accounts.add_user("a", "password1", "annotator")
    accounts.add_user("b", "password1", "annotator")
    table = builder.with_ids(_table(), None)
    ws = builder.create_project("My Study!", "choice", "Pick one", table, ["text"], "me",
                                batch_size=4, labels=["Yes", "No"], people=["a", "b"], per_batch=2)
    assert ws.id == "my_study" and is_registered(ws)
    assert list_batches(ws) == ["batch_001", "batch_002", "batch_003"]
    assert len(load_batch(ws, "batch_003")) == 2
    assert accounts.assigned_batches(ws, "a") == accounts.assigned_batches(ws, "b")
    assert batch_status(ws, "a", "batch_001")["total"] == 4

    builder.add_labels(ws, ["Maybe"])
    assert [v for v in load_workspace(ws.root).projects[0].verdicts] == ["Yes", "No", "Maybe"]
    assert builder.add_data(ws, _table(3), None) == ["batch_004"]
    assert load_batch(ws, "batch_004")["id"].astype(str).tolist() == ["11", "12", "13"]
    with pytest.raises(ValueError):
        builder.add_data(ws, pd.DataFrame({"id": ["1"], "text": ["x"]}), "id")


def test_interface_project_leaves_other_workspaces_alone(workspace):
    registry.add(workspace)
    before = (workspace / "quillbench_workspace.yaml").read_bytes()
    ws = builder.create_project("demo", "text", "", builder.with_ids(_table(2), None), ["text"], "me")
    assert ws.id == "demo_2" and not list(workspace.glob("quillbench_assign*"))
    assert (workspace / "quillbench_workspace.yaml").read_bytes() == before


def is_registered(ws):
    return any(w.id == ws.id for w in registry.load_all()[0])


def test_bad_project_leaves_nothing(studio):
    with pytest.raises(ValueError):
        builder.create_project("x", "choice", "", _table(1), ["text"], "me", labels=["only"])
    assert not list(builder.projects_root().glob("*"))


def test_empty_project_takes_csv_later(studio):
    ws = builder.create_project("Later", "choice", "", None, [], "me", labels=["A", "B"])
    assert builder.is_app_project(ws) and not list_batches(ws)
    assert builder.add_data(ws, _table(4), None, batch_size=2, people=["ann", "ben"],
                            display=["text"]) == ["batch_001", "batch_002"]
    ws = load_workspace(ws.root)
    assert [f.column for f in ws.projects[0].display] == ["text"]
    assert accounts.load_assignments(ws)["ben"]["batches"] == ["batch_002"]
    with pytest.raises(ValueError, match="missing"):
        builder.add_data(ws, pd.DataFrame({"other": ["x"]}), None)


def test_manifest_in_finds_older_names(tmp_path):
    from quillbench.config import MANIFEST_NAME, manifest_in

    assert manifest_in(tmp_path) == tmp_path / MANIFEST_NAME
    for old in ("pas_workspace.yaml", "labelwise_workspace.yaml"):
        (tmp_path / old).write_text("x", encoding="utf-8")
    assert manifest_in(tmp_path).name in ("pas_workspace.yaml", "labelwise_workspace.yaml")
    (tmp_path / MANIFEST_NAME).write_text("x", encoding="utf-8")
    assert manifest_in(tmp_path) == tmp_path / MANIFEST_NAME
