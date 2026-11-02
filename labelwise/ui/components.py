"""Pieces every annotation screen shares, so all task types look and behave alike.

Layout of an annotation screen, top to bottom:

    work bar      batch picker · progress · Instructions · Settings   (app.py + toolbar())
    item card     ← Previous | jump to item | Next →, then the item itself
    answer bar    sticky at the bottom of the screen: the answer buttons
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from ..config import ProjectConfig
from ..storage import blank_if_nan, find_step
from ..tasks import TaskSpec
from .shortcuts import HELP_HTML
from .theme import card, progress_chip


def toolbar(bar, project: ProjectConfig, n_labeled: int, total: int, *, box_sizes: bool) -> bool:
    """Fill the work bar's progress, instructions and settings slots.

    Returns whether Previous/Next should skip answered items. Settings live in a
    popover so the item, not the controls, fills the screen.
    """
    with bar[1]:
        progress_chip(n_labeled, total)
    with bar[2]:
        with st.popover("Instructions", width="stretch", key="labelwise_pop_instructions",
                        disabled=not project.instructions.strip()):
            st.markdown(project.instructions or "No instructions for this project.")
    with bar[3]:
        with st.popover("Settings", width="stretch", key="labelwise_pop_settings"):
            skip = st.checkbox("Previous/Next skip answered items", key="labelwise_skip_answered")
            if box_sizes:
                st.session_state.setdefault("ui_cypher_h", 320)
                st.session_state.setdefault("ui_result_h", 400)
                st.session_state.ui_cypher_h = st.slider("Query box height", 150, 900,
                                                         st.session_state.ui_cypher_h, 10)
                st.session_state.ui_result_h = st.slider("Result box height", 150, 900,
                                                         st.session_state.ui_result_h, 10)
            st.session_state.setdefault("ui_note_h", 90)
            st.session_state.ui_note_h = st.slider("Note box height", 68, 400,
                                                   st.session_state.ui_note_h, 10)
            st.markdown(HELP_HTML, unsafe_allow_html=True)
            st.markdown(legend_html(project.task_spec), unsafe_allow_html=True)
    return bool(skip)


def legend_html(spec: TaskSpec) -> str:
    if spec.kind == "labels":
        parts = ["⚪ not answered"] + [f"{lab.glyph} {html.escape(lab.text)}" for lab in spec.labels]
    else:
        parts = ["⚪ not answered", "🔵 answered"]
    return '<div class="nav-legend">' + " &nbsp; ".join(parts) + "</div>"


def item_nav(df: pd.DataFrame, idx: int, total: int, labeled: pd.Series, skip: bool,
             batch: str, project: ProjectConfig) -> None:
    """← Previous | jump to any item | Next →, in one row."""
    spec = project.task_spec
    has_local = "local_id" in df.columns

    def label_for(i: int) -> str:
        row = df.iloc[i]
        glyph = spec.glyph_for(blank_if_nan(row.get("human_verdict", "")))
        local = (blank_if_nan(row["local_id"]) or i + 1) if has_local else i + 1
        return f"{glyph}  Item {local}  ·  {i + 1} of {total}"

    nav = st.container(key="labelwise_nav")
    c1, c2, c3 = nav.columns([1, 4, 1], vertical_alignment="center")
    with c1:
        if st.button("← Previous", width="stretch", key=f"nav_prev_{batch}", disabled=idx == 0):
            st.session_state.query_idx = find_step(total, idx, labeled, False, skip)
            st.rerun()
    with c2:
        # Keyed on the position and the answered count, so the box remounts after any
        # navigation or new answer and never holds a stale selection or label.
        # Options are positions, labels are drawn: a label changes when the item is
        # answered, and a keyed box holding the old label text would no longer match.
        picked = st.selectbox("Jump to item", list(range(total)), index=idx,
                              format_func=label_for, label_visibility="collapsed",
                              key=f"navselect_{batch}_{idx}_{int(labeled.sum())}")
        if picked != idx:
            st.session_state.query_idx = picked
            st.rerun()
    with c3:
        if st.button("Next →", width="stretch", key=f"nav_next_{batch}", disabled=idx >= total - 1):
            st.session_state.query_idx = find_step(total, idx, labeled, True, skip)
            st.rerun()


def answer_head(title: str, spec: TaskSpec, current: str) -> None:
    recorded = ""
    if spec.is_labeled(current):
        text = next((lab.text for lab in spec.labels if lab.value == current), current)
        recorded = f'<span class="recorded">Recorded: <b>{html.escape(str(text))}</b></span>'
    st.markdown(f'<div class="answer-head"><span class="label">{html.escape(title)}</span>'
                f'{recorded}</div>', unsafe_allow_html=True)


def answer_buttons(spec: TaskSpec, current: str, batch: str, qid) -> str | None:
    """One button per label, coloured by meaning, plus Skip.

    Returns the clicked label value, ``""`` for Skip, or None. The key carries the
    shortcut number (``verdict_<n>_``), the colour (``_x<tone>``) and whether it is
    the recorded answer (``S``), which is all the stylesheet and the keyboard
    handler need.
    """
    labels = list(spec.labels)
    cols = st.columns(len(labels) + 1)
    clicked = None
    for n, (col, lab) in enumerate(zip(cols, labels), start=1):
        sel = "S" if lab.value == current else ""
        # Blinded A/B: every option looks the same, since a coloured option is a nudge.
        tone = "neu" if spec.view == "pair" else lab.tone
        with col:
            if st.button(f"{lab.text}", width="stretch", help=lab.help or f"Key {n}",
                         key=f"verdict_{n}_x{tone}{sel}_{batch}_{qid}"):
                clicked = lab.value
    with cols[-1]:
        if st.button("Skip", width="stretch", help="Key S — move on without answering",
                     key=f"verdict_skip_{batch}_{qid}"):
            clicked = ""
    return clicked


def needs_note(spec: TaskSpec, value: str, note: str) -> bool:
    if value in spec.reason_required and not note.strip():
        st.warning("This answer needs a note. Write one in the note box, then click again.")
        return True
    return False


def done_card(n_labeled: int, total: int, next_batch: str | None) -> None:
    """Shown at the top of a finished batch, with a way on to the next one."""
    if total == 0 or n_labeled < total:
        return
    body = "Every item has an answer. You can still go back and change any of them."
    c1, c2 = st.columns([4, 1], vertical_alignment="center")
    with c1:
        card("done", "✓ Batch complete", body)
    with c2:
        if next_batch and st.button(f"Next batch →", type="primary", width="stretch",
                                    help=f"Open {next_batch}", key="labelwise_next_batch"):
            st.session_state.active_batch = next_batch
            st.session_state.query_idx = 0
            st.rerun()


def parse_expected(raw):
    """An item's expected answer, from a practice item's ``expected`` column:
    a label, a number, or a range written ``0.6-0.8``."""
    text = blank_if_nan(raw).strip()
    if not text:
        return None
    if "-" in text[1:]:
        lo, hi = text[0] + text[1:].split("-", 1)[0], text[1:].split("-", 1)[1]
        try:
            return [float(lo), float(hi)]
        except ValueError:
            pass
    return text


def practice_feedback(project: ProjectConfig, row, value: str, total: int, idx: int,
                      labeled: pd.Series) -> None:
    """After a practice answer: was it in line with the reference, and why."""
    from ..quality import _check_ok

    expected = parse_expected(row.get("expected", ""))
    why = html.escape(blank_if_nan(row.get("explanation", "")))
    if expected is None:
        card("", "Answer saved", why or "This practice item has no reference answer.")
    else:
        try:
            ok = _check_ok(project, value, expected)
        except (TypeError, ValueError):
            ok = False
        shown = f"{expected[0]:g}–{expected[1]:g}" if isinstance(expected, list) else str(expected)
        title = "✓ In line with the reference" if ok else "✗ Different from the reference"
        card("good" if ok else "bad", title,
             f"You answered <b>{html.escape(value)}</b>; the reference is <b>{html.escape(shown)}</b>."
             + (f"<br>{why}" if why else ""))
    if st.button("Next practice item →", type="primary", key="labelwise_practice_next"):
        st.session_state.pop("labelwise_feedback", None)
        st.session_state.query_idx = find_step(total, idx, labeled, True, False)
        st.rerun()
