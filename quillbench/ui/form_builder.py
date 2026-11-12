"""Admin: design the form people fill in for a benchmark-creation project."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import accounts, benchmark, categories, connections
from ..builder import clean_labels
from ..config import WorkspaceConfig
from . import creator


def _draft_key(ws: WorkspaceConfig) -> str:
    return f"fb_draft_{ws.id}"


def _draft(ws: WorkspaceConfig) -> benchmark.Form:
    key = _draft_key(ws)
    if key not in st.session_state:
        st.session_state[key] = benchmark.load_form(ws)
    return st.session_state[key]


def _k(ws: WorkspaceConfig, slot: benchmark.Slot, name: str) -> str:
    return f"fb_{ws.id}_{slot.id}_{name}"


def _collect(ws: WorkspaceConfig, form: benchmark.Form) -> None:
    """Copy what is typed in the slot editors into the draft."""
    ss = st.session_state
    for s in form.slots:
        s.label = ss.get(_k(ws, s, "label"), s.label)
        s.help = ss.get(_k(ws, s, "help"), s.help)
        if s.column:
            s.required = ss.get(_k(ws, s, "required"), s.required)
        if s.type in ("text", "schema"):
            s.text = ss.get(_k(ws, s, "text"), s.text)
        if s.type in ("choice", "dropdown", "multi") and _k(ws, s, "options") in ss:
            s.options = clean_labels(ss[_k(ws, s, "options")])
        if s.type == "rating":
            s.minimum = ss.get(_k(ws, s, "min"), s.minimum)
            s.maximum = ss.get(_k(ws, s, "max"), s.maximum)
            s.step = ss.get(_k(ws, s, "step"), s.step)
        if s.type == "result":
            s.confirm = ss.get(_k(ws, s, "confirm"), s.confirm)
            if _k(ws, s, "verdicts") in ss:
                s.verdicts = clean_labels(ss[_k(ws, s, "verdicts")])
            s.accept = [v for v in ss.get(_k(ws, s, "accept"), s.accept) if v in s.verdicts]
            s.scored = [v for v in ss.get(_k(ws, s, "scored"), s.scored) if v in s.verdicts]
        if s.type == "examples" and _k(ws, s, "examples") in ss:
            table = ss[f"{_k(ws, s, 'examples')}_value"]
            s.examples = [{k: str(v) for k, v in row.items() if str(v or "").strip()}
                          for row in table.to_dict("records")
                          if str(row.get("question") or "").strip()]
    ss_form = st.session_state
    form.connection = ss_form.get(f"fb_{ws.id}_conn", form.connection) or ""
    form.participants = list(ss_form.get(f"fb_{ws.id}_people", form.participants))
    form.threshold = ss_form.get(f"fb_{ws.id}_thr", round(form.threshold * 100)) / 100
    form.min_rows = int(ss_form.get(f"fb_{ws.id}_rows", form.min_rows))
    form.min_total = int(ss_form.get(f"fb_{ws.id}_min_total", form.min_total))
    table = ss_form.get(f"fb_{ws.id}_points_value")
    if table is not None:
        form.points, form.targets = {}, {}
        for row in table.to_dict("records"):
            cid = row["id"]
            pts = int(row.get("Points") or 0)
            if pts != categories.BY_ID[cid].points:
                form.points[cid] = pts
            if int(row.get("Minimum per person") or 0) > 0:
                form.targets[cid] = int(row["Minimum per person"])


def _slot_editor(ws: WorkspaceConfig, form: benchmark.Form, i: int) -> None:
    s = form.slots[i]
    title, is_input = benchmark.SLOT_TYPES[s.type]
    with st.container(border=True):
        head, up, down, rm = st.columns([8, 1, 1, 1], vertical_alignment="center")
        head.markdown(f"**{i + 1}. {title}** · `{s.id}`")
        if up.button("↑", key=_k(ws, s, "up"), disabled=i == 0, help="Move up"):
            _collect(ws, form)
            form.slots[i - 1], form.slots[i] = form.slots[i], form.slots[i - 1]
            st.rerun()
        if down.button("↓", key=_k(ws, s, "down"), disabled=i == len(form.slots) - 1, help="Move down"):
            _collect(ws, form)
            form.slots[i + 1], form.slots[i] = form.slots[i], form.slots[i + 1]
            st.rerun()
        if rm.button("✕", key=_k(ws, s, "rm"), disabled=s.type in benchmark.CORE,
                     help="Remove (question, query and result are always part of the form)"):
            _collect(ws, form)
            form.slots.pop(i)
            st.rerun()

        c1, c2 = st.columns([2, 3])
        c1.text_input("Label", value=s.label, key=_k(ws, s, "label"))
        c2.text_input("Help text (optional)", value=s.help, key=_k(ws, s, "help"))
        if s.type == "text":
            st.text_area("Text (Markdown)", value=s.text, key=_k(ws, s, "text"), height=120)
        elif s.type == "schema":
            st.text_area("Note above the schema (optional)", value=s.text, key=_k(ws, s, "text"),
                         height=70, help="The tables and columns are read live from the database.")
        elif s.type in ("choice", "dropdown", "multi"):
            st.text_area("Options (one per line)", value="\n".join(s.options), key=_k(ws, s, "options"),
                         height=100)
        elif s.type == "rating":
            a, b, c = st.columns(3)
            a.number_input("Lowest", value=float(s.minimum), key=_k(ws, s, "min"))
            b.number_input("Highest", value=float(s.maximum), key=_k(ws, s, "max"))
            c.number_input("Step", value=float(s.step), min_value=0.001, key=_k(ws, s, "step"))
        elif s.type == "result":
            st.text_input("Question above the buttons", value=s.confirm, key=_k(ws, s, "confirm"))
            st.text_area("Verdict buttons (one per line)", value="\n".join(s.verdicts),
                         key=_k(ws, s, "verdicts"), height=90,
                         help="Every entry stores which button the person chose, in the 'verdict' column.")
            typed = clean_labels(st.session_state.get(_k(ws, s, "verdicts"), "\n".join(s.verdicts)))
            a, b = st.columns(2)
            a.multiselect("Can be added when marked", typed, default=[v for v in s.accept if v in typed],
                          key=_k(ws, s, "accept"))
            b.multiselect("Earns points when marked", typed, default=[v for v in s.scored if v in typed],
                          key=_k(ws, s, "scored"))
        elif s.type == "examples":
            st.caption("One row per example. Rows without a question are dropped.")
            start = pd.DataFrame(s.examples or [], columns=["question", "query", "note"]).fillna("")
            value = st.data_editor(start, key=_k(ws, s, "examples"), num_rows="dynamic",
                                   width="stretch", hide_index=True,
                                   column_config={"question": st.column_config.TextColumn("Question", width="medium"),
                                                  "query": st.column_config.TextColumn("Query", width="large"),
                                                  "note": st.column_config.TextColumn("Note")})
            st.session_state[f"{_k(ws, s, 'examples')}_value"] = value
        if s.column:
            st.checkbox("Required", value=s.required, key=_k(ws, s, "required"))


def _settings(ws: WorkspaceConfig, form: benchmark.Form) -> None:
    conns = connections.load_all()
    ids = list(conns)
    c1, c2 = st.columns(2)
    if form.connection and form.connection not in ids:
        st.warning(f"The connection '{form.connection}' no longer exists. Choose another.")
    c1.selectbox("Database connection", ids, index=ids.index(form.connection) if form.connection in ids else None,
                 key=f"fb_{ws.id}_conn", format_func=lambda c: f"{conns[c].name} ({connections.ENGINES[conns[c].engine]})",
                 placeholder="Add one under Admin → Connections",
                 help="Set up by admins. People see only its name, never the login details.")
    users = accounts.load_users()
    people = sorted(u for u, r in users.items() if r.get("active", True))
    c2.multiselect("People who write entries", people,
                   default=[p for p in form.participants if p in people], key=f"fb_{ws.id}_people")
    c3, c4 = st.columns(2)
    c3.slider("Not added when the question is this similar to an existing one", 50, 100,
              round(form.threshold * 100), format="%d%%", key=f"fb_{ws.id}_thr")
    c4.number_input("Rows the query must return", 0, 1000, int(form.min_rows), key=f"fb_{ws.id}_rows",
                    help="1 means an empty result cannot be added.")


def _points(ws: WorkspaceConfig, form: benchmark.Form) -> None:
    """Points per category and the minimum each person should reach."""
    conn = connections.get(form.connection)
    language = conn.language if conn else "sql"
    st.caption("The system finds the categories in each query by itself. Here you decide what "
               "each category is worth and the minimum points each person should reach in it "
               "(0 = no minimum). An entry earns the points of its most valuable category only. "
               "Its complexity (Easy / Medium / Hard / Very hard) is fixed by the system.")
    start = pd.DataFrame([{"id": c.id, "Category": c.label, "Example": c.hint,
                           "Points": form.points_for(c.id),
                           "Minimum per person": form.targets.get(c.id, 0)}
                          for c in categories.for_language(language)])
    value = st.data_editor(start, key=f"fb_{ws.id}_points", width="stretch", hide_index=True,
                           disabled=["id", "Category", "Example"], column_order=["Category", "Points",
                                                                                "Minimum per person", "Example"],
                           column_config={"Points": st.column_config.NumberColumn(min_value=0, max_value=100, step=1),
                                          "Minimum per person": st.column_config.NumberColumn(
                                              min_value=0, max_value=1000, step=1)})
    st.session_state[f"fb_{ws.id}_points_value"] = value
    st.number_input("Minimum total points per person (0 = none)", 0, 100000, int(form.min_total),
                    key=f"fb_{ws.id}_min_total")


def render(ws: WorkspaceConfig) -> None:
    form = _draft(ws)
    note = st.session_state.pop(f"fb_note_{ws.id}", None)
    if note:
        st.success(note)
    st.markdown("**Settings**")
    _settings(ws, form)
    with st.expander("**Categories and points**"):
        _points(ws, form)
    st.divider()
    st.markdown("**Form**")
    for i in range(len(form.slots)):
        _slot_editor(ws, form, i)

    a, b = st.columns([3, 1], vertical_alignment="bottom")
    have = {s.type for s in form.slots}
    choices = [t for t in benchmark.SLOT_TYPES if t not in benchmark.CORE or t not in have]
    kind = a.selectbox("Add a slot", choices, format_func=lambda t: benchmark.SLOT_TYPES[t][0],
                       key=f"fb_{ws.id}_newtype")
    if b.button("Add slot", key=f"fb_{ws.id}_add", width="stretch"):
        _collect(ws, form)
        slot = benchmark.Slot(benchmark.new_slot_id(form, kind), kind,
                              benchmark.SLOT_TYPES[kind][0].split(" (")[0])
        if kind in ("choice", "dropdown", "multi"):
            slot.options = ["Option 1", "Option 2"]
        form.slots.append(slot)
        st.rerun()

    st.divider()
    s1, s2, s3 = st.columns(3)
    if s1.button("Save form", type="primary", key=f"fb_{ws.id}_save", width="stretch"):
        _collect(ws, form)
        problems = benchmark.check_form(form)
        if problems:
            for p in problems:
                st.error(p)
        else:
            benchmark.save_form(ws, form)
            st.session_state[f"fb_note_{ws.id}"] = "Form saved. People see the new version on their next page load."
            st.rerun()
    if s2.button("Discard changes", key=f"fb_{ws.id}_discard", width="stretch"):
        for k in [k for k in st.session_state if str(k).startswith(f"fb_{ws.id}_") or k == _draft_key(ws)]:
            del st.session_state[k]
        st.rerun()
    preview = s3.toggle("Preview saved form", key=f"fb_{ws.id}_preview")
    if preview:
        st.caption("This is the saved form as people see it. Entries added here are real.")
        creator.render(ws, st.session_state.get("annotator", ""), prefix="preview")
