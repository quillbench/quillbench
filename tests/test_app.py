"""Headless run of the real web app with Streamlit's AppTest."""

import time
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from labelwise import accounts, registry
from labelwise.config import load_workspace

APP = str(Path(__file__).resolve().parents[1] / "labelwise" / "ui" / "app.py")


@pytest.fixture
def ready(workspace, studio):
    registry.add(workspace)
    accounts.add_user("alice", "password1", "annotator")
    accounts.add_user("boss", "password1", "admin")
    ws = load_workspace(workspace)
    accounts.save_assignments(ws, {"alice": {"batches": ["rate_01", "pick_01", "b_01"]}})
    return workspace


def _login(at, name):
    # Signed in directly: Streamlit's AppTest keeps the sign-in form's widgets in
    # its tree after the page reruns, which breaks the next run. The sign-in form
    # itself is covered by test_login_page and test_wrong_password.
    at.session_state["logged_in"] = True
    at.session_state["annotator"] = name
    at.session_state["auth_issued"] = int(time.time())
    at.run(timeout=30)
    assert not at.exception, at.exception
    return at


def _batch_box(at):
    return next(s for s in at.selectbox if s.label == "Batch")


def _button(at, prefix):
    return next(b for b in at.button if str(b.key or "").startswith(prefix))


def test_password_change_ends_live_session(ready):
    at = _login(AppTest.from_file(APP), "alice")
    accounts.set_password("alice", "brand new pass")
    at.run(timeout=30)
    assert not at.session_state.logged_in


def test_login_page(ready):
    at = AppTest.from_file(APP)
    at.run(timeout=30)
    assert not at.exception
    assert [t.label for t in at.text_input] == ["User ID", "Password"]


def test_right_password_signs_in(ready):
    at = AppTest.from_file(APP)
    at.run(timeout=30)
    at.text_input[0].input("alice")
    at.text_input[1].input("password1")
    at.button[0].click()
    at.run(timeout=30)
    assert at.session_state.logged_in and at.session_state.annotator == "alice"


def test_wrong_password(ready):
    at = AppTest.from_file(APP)
    at.run(timeout=30)
    at.text_input[0].input("alice")
    at.text_input[1].input("nope-nope")
    at.button[0].click()
    at.run(timeout=30)
    assert any("Wrong user ID or password" in e.value for e in at.error)


def test_annotator_rates_an_item(ready):
    at = _login(AppTest.from_file(APP), "alice")
    # the first assigned batch is the rating batch
    assert "rate_01" in _batch_box(at).value
    save = _button(at, "rating_save_")
    assert save.disabled                       # an untouched default cannot be saved
    rating = next(s for s in at.slider if s.label == "Rating")
    rating.set_value(0.7)
    at.run(timeout=30)
    _button(at, "rating_save_").click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    saved = pd.read_csv(ready / "progress" / "alice__rate_01.csv")
    assert saved.loc[saved["id"] == "q1", "human_verdict"].iloc[0] == 0.7
    assert at.session_state.query_idx == 1


def test_annotator_choice_needs_note(ready):
    at = _login(AppTest.from_file(APP), "alice")
    batch_box = _batch_box(at)
    batch_box.select(next(o for o in batch_box.options if "pick_01" in o))
    at.run(timeout=30)
    _button(at, "verdict_3_").click()          # MAYBE requires a note
    at.run(timeout=30)
    assert any("needs a note" in w.value for w in at.warning)
    _button(at, "verdict_1_").click()          # YES
    at.run(timeout=30)
    saved = pd.read_csv(ready / "progress" / "alice__pick_01.csv")
    assert saved.loc[saved["id"] == "p0", "human_verdict"].iloc[0] == "YES"


def test_query_review_task(ready):
    at = _login(AppTest.from_file(APP), "alice")
    batch_box = _batch_box(at)
    batch_box.select(next(o for o in batch_box.options if "b_01" in o))
    at.run(timeout=30)
    assert not at.exception, at.exception
    _button(at, "verdict_1_").click()          # Correct
    at.run(timeout=30)
    saved = pd.read_csv(ready / "progress" / "alice__b_01.csv")
    assert saved["human_verdict"].iloc[0] == "CORRECT"


def test_admin_sees_admin_tab(ready):
    at = _login(AppTest.from_file(APP), "boss")
    labels = [t.label for t in at.tabs]
    assert "Admin" in labels and "Progress" in labels


def test_buttons_coloured_by_meaning_and_pair_neutral(ready):
    at = _login(AppTest.from_file(APP), "alice")
    box = _batch_box(at)
    box.select(next(o for o in box.options if "b_01" in o))
    at.run(timeout=30)
    keys = [str(b.key) for b in at.button if str(b.key or "").startswith("verdict_")]
    assert any("_xgood_" in k for k in keys) and any("_xbad_" in k for k in keys)


def test_next_batch_after_finishing(ready):
    (ready / "progress").mkdir(exist_ok=True)
    (ready / "progress" / "alice__b_01.csv").write_text(
        "id,nl_query,gold_cypher,human_verdict,note,paraphrase\n1,how many?,MATCH,CORRECT,,\n")
    at = AppTest.from_file(APP)
    at.session_state["active_batch"] = "b_01"
    at = _login(at, "alice")
    assert any("Batch complete" in m.value for m in at.markdown)
    _button(at, "labelwise_next_batch").click()
    at.run(timeout=30)
    assert at.session_state.active_batch in ("rate_01", "pick_01")


def test_opens_first_unfinished_batch(ready):
    (ready / "progress").mkdir(exist_ok=True)
    (ready / "progress" / "alice__rate_01.csv").write_text(
        "id,question,context,human_verdict,note,paraphrase\nq1,a,b,0.5,,\nq2,a,b,0.5,,\nc1,a,b,0.5,,\n")
    at = _login(AppTest.from_file(APP), "alice")
    assert at.session_state.active_batch == "pick_01"


def test_practice_feedback(ready):
    text = (ready / "labelwise_workspace.yaml").read_text().replace(
        "    checks: {c1: [0.9, 1.0]}", "    practice: true")
    (ready / "labelwise_workspace.yaml").write_text(text)
    (ready / "batches" / "rate_01.csv").write_text(
        "id,question,context,expected,explanation\nq1,Is it hot?,x,0.8-1.0,36 degrees is hot.\n")
    at = _login(AppTest.from_file(APP), "alice")
    next(s for s in at.slider if s.label == "Rating").set_value(0.3)
    at.run(timeout=30)
    _button(at, "rating_save_").click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Different from the reference" in body and "36 degrees is hot." in body


def test_first_visitor_becomes_admin(studio):
    at = AppTest.from_file(APP)
    at.run(timeout=30)
    at.text_input[0].input("owner")
    at.text_input[1].input("password1")
    at.button[0].click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    assert at.session_state.logged_in and accounts.is_admin("owner", accounts.load_users())
    assert any(t.label == "Project name" for t in at.text_input)
