"""
Annotation screen for generic tasks declared in a manifest: rating (a numeric
scale such as 0-1 or 1-5), choice (any set of labels) and text (a free answer).

Items come from the batch file (CSV or JSON Lines). The manifest's ``display:``
list says which columns to show and how (text, markdown, code, json, table or
chart); without it every item column is shown.
"""

from __future__ import annotations

import html
import json

import pandas as pd
import streamlit as st

from ..config import DisplayField, ProjectConfig, WorkspaceConfig
from ..storage import (
    PROGRESS_COLUMNS,
    blank_if_nan,
    find_step,
    get_progress_df,
    labeled_mask,
    load_batch,
    set_verdict,
)
from .components import (
    answer_buttons,
    answer_head,
    done_card,
    item_nav,
    needs_note,
    practice_feedback,
    toolbar,
)
from .query_review import item_timer


def _fields(project: ProjectConfig, df: pd.DataFrame) -> list[DisplayField]:
    if project.display:
        return list(project.display)
    hidden = {"id", "local_id", "expected", "explanation", *PROGRESS_COLUMNS}
    return [DisplayField(c) for c in df.columns if c not in hidden]


def _series(value) -> pd.DataFrame | None:
    """Chart data from a cell: a JSON list of numbers (x = 0, 1, ...), a JSON
    object ``{"x": [...], "y": [...]}``, or a list of ``{"x":, "y":}`` rows."""
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return None
    if isinstance(data, dict) and "y" in data:
        ys = list(data["y"])
        xs = list(data.get("x", range(len(ys))))
        return pd.DataFrame({"x": xs, "y": ys})
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return pd.DataFrame(data)[["x", "y"]]
    if isinstance(data, list):
        return pd.DataFrame({"x": range(len(data)), "y": data})
    return None


def _chart(field: DisplayField, value) -> None:
    import altair as alt

    df = _series(value)
    if df is None or df.empty:
        st.caption("No chart data for this item.")
        return
    opts = field.chart
    df["y"] = pd.to_numeric(df["y"], errors="coerce")
    x_title, y_title = str(opts.get("x_label", "")), str(opts.get("y_label", ""))
    x_type = "O" if opts.get("x_ordinal", True) else "Q"
    base = alt.Chart(df)
    if opts.get("kind", "bar") == "line":
        marks = base.mark_line(point=True, color="#2B3A67").encode(
            x=alt.X(f"x:{x_type}", title=x_title), y=alt.Y("y:Q", title=y_title))
    else:
        marks = base.mark_bar(color="#5468A8", cornerRadiusTopLeft=2, cornerRadiusTopRight=2).encode(
            x=alt.X(f"x:{x_type}", title=x_title, axis=alt.Axis(labelAngle=0, labelOverlap="greedy")),
            y=alt.Y("y:Q", title=y_title),
            tooltip=[alt.Tooltip("x", title=x_title or "x"), alt.Tooltip("y", title=y_title or "value")])
    layers = [marks]
    lines = []
    for ln in opts.get("lines", []) or []:
        if isinstance(ln, dict):
            lines.append({"v": float(ln["value"]), "label": str(ln.get("label", ln["value"]))})
        else:
            lines.append({"v": float(ln), "label": f"{float(ln):g}"})
    if lines:
        ldf = pd.DataFrame(lines)
        rule = alt.Chart(ldf).mark_rule(color="#D9962B", strokeDash=[5, 4], strokeWidth=1.5).encode(y="v:Q")
        text = alt.Chart(ldf).mark_text(align="left", dx=4, dy=-6, color="#A86B12", fontSize=11).encode(
            y="v:Q", x=alt.value(2), text="label:N")
        layers += [rule, text]
    missing = int(df["y"].isna().sum())
    chart = (alt.layer(*layers).properties(height=int(opts.get("height", 220)))
             .configure(background="transparent")
             .configure_view(strokeWidth=0)
             .configure_axis(labelColor="#5B6576", titleColor="#5B6576", gridColor="#E6E9EF",
                             domainColor="#DDE2EA", labelFont="Inter", titleFont="Inter"))
    st.altair_chart(chart, use_container_width=True, theme=None)
    if missing:
        st.caption(f"{missing} missing value(s) shown as gaps.")


def _show_field(field: DisplayField, value) -> None:
    text = blank_if_nan(value)
    label = field.label or field.column.replace("_", " ").capitalize()

    def body() -> None:
        if field.as_ == "chart":
            _chart(field, value)
        elif field.as_ == "markdown":
            st.markdown(text or "—")
        elif field.as_ == "code":
            st.code(text or "—", language=field.language or None, wrap_lines=True)
        elif field.as_ == "json":
            try:
                st.json(json.loads(text) if isinstance(text, str) else text)
            except (TypeError, ValueError):
                st.code(text or "—")
        elif field.as_ == "table":
            try:
                rows = json.loads(text) if isinstance(text, str) else text
                st.table(pd.DataFrame(rows))
            except (TypeError, ValueError):
                st.code(text or "—")
        else:
            st.markdown(f'<div class="field-text">{html.escape(text or "—")}</div>',
                        unsafe_allow_html=True)

    if field.collapsed:
        with st.expander(label):
            body()
    else:
        st.markdown(f'<div class="field-label">{html.escape(label)}</div>', unsafe_allow_html=True)
        body()


def _fmt(value: float, step: float) -> str:
    text = f"{step:g}"
    decimals = len(text.split(".")[1]) if "." in text else 0
    return f"{value:.{decimals}f}"


def _marks_html(r) -> str:
    if not r.marks:
        return ""
    span = r.maximum - r.minimum
    parts = []
    for i, (v, word) in enumerate(r.marks):
        pos = (v - r.minimum) / span * 100
        cls = "first" if pos <= 0.5 else ("last" if pos >= 99.5 else "")
        if not cls and len(r.marks) > 3 and abs(pos - 50) > 1:
            cls = "minor"          # hidden on phones, where five words do not fit
        parts.append(f'<span class="{cls}" style="left:{pos:.2f}%">{html.escape(word)}</span>')
    return '<div class="rating-marks">' + "".join(parts) + "</div>"


def render(ws: WorkspaceConfig, annotator: str, active_batch: str, bar,
           next_batch: str | None = None) -> None:
    project = ws.project_for_batch(active_batch)
    spec = project.task_spec
    if st.session_state.active_batch != active_batch:
        st.session_state.active_batch = active_batch
        st.session_state.query_idx = 0

    batch_df = load_batch(ws, active_batch)
    if batch_df.empty or "id" not in batch_df.columns:
        st.error(f"Batch '{active_batch}' is missing, empty or has no 'id' column.")
        return
    df = get_progress_df(ws, annotator, active_batch, batch_df)
    total = len(df)
    labeled = labeled_mask(df, project)
    n_labeled = int(labeled.sum())
    skip_labeled = toolbar(bar, project, n_labeled, total, box_sizes=False)
    if project.practice:
        st.caption("Practice round — after each answer you will see the reference answer and why. "
                   "Practice answers are kept apart from the real study.")
    else:
        done_card(n_labeled, total, next_batch)

    idx = min(max(st.session_state.query_idx, 0), total - 1)
    row = df.iloc[idx]
    qid = row["id"]
    seconds_open = item_timer(active_batch, qid)
    current = blank_if_nan(row.get("human_verdict", ""))
    feedback = st.session_state.get("labelwise_feedback")
    showing_feedback = project.practice and feedback and feedback[:2] == (active_batch, str(qid))

    with st.container(key="labelwise_item"):
        item_nav(df, idx, total, labeled, skip_labeled, active_batch, project)
        local = blank_if_nan(row.get("local_id", "")) or idx + 1
        badges = f'<span class="meta-badge primary">Item {html.escape(str(local))}</span>'
        if spec.is_labeled(current):
            badges += '<span class="meta-badge done">✓ answered</span>'
        badges += f'<span class="meta-badge subtle">ID {html.escape(str(qid))}</span>'
        st.markdown(badges, unsafe_allow_html=True)
        fields = _fields(project, df)
        for i, field in enumerate(fields):
            if field.column in row.index:
                if i == 0 and field.as_ == "text":
                    # The first field is what is being judged: give it the reading size.
                    if field.label:
                        st.markdown(f'<div class="field-label">{html.escape(field.label)}</div>',
                                    unsafe_allow_html=True)
                    st.markdown(f'<div class="nl-question">{html.escape(blank_if_nan(row[field.column]) or "—")}'
                                f'</div>', unsafe_allow_html=True)
                else:
                    _show_field(field, row[field.column])
        note_val = st.text_area(spec.note_label, value=blank_if_nan(row.get("note", "")),
                                placeholder=spec.note_placeholder,
                                height=st.session_state.get("ui_note_h", 90),
                                key=f"note_{active_batch}_{qid}")

    if showing_feedback:
        practice_feedback(project, row, feedback[2], total, idx, labeled)
        return

    def save(value: str) -> None:
        if needs_note(spec, value, note_val):
            return
        set_verdict(ws, annotator, active_batch, qid, value, note_val, batch_df,
                    seconds=seconds_open)
        if project.practice:
            st.session_state["labelwise_feedback"] = (active_batch, str(qid), value)
        else:
            st.session_state.query_idx = find_step(total, idx, labeled, True, skip_labeled)
        st.rerun()

    with st.container(key="labelwise_answerbar"):
        if spec.kind == "rating":
            _rating_bar(spec, current, active_batch, qid, total, idx, labeled, skip_labeled, save)
        elif spec.kind == "labels":
            answer_head("Your answer", spec, current)
            value = answer_buttons(spec, current, active_batch, qid)
            if value:
                save(value)
            elif value == "":
                st.session_state.query_idx = find_step(total, idx, labeled, True, skip_labeled)
                st.rerun()
        else:
            answer_head("Your answer", spec, current)
            answer = st.text_area("Answer", value=current, height=100, label_visibility="collapsed",
                                  key=f"answer_{active_batch}_{qid}")
            if st.button("Save", type="primary", key=f"rating_save_{active_batch}_{qid}"):
                if answer.strip():
                    save(answer.strip())
                else:
                    st.warning("Write an answer first.")


def _rating_bar(spec, current, batch, qid, total, idx, labeled, skip, save) -> None:
    r = spec.rating
    key = f"rating_{batch}_{qid}"
    touched_key = f"touched_{key}"
    try:
        start = float(current)
        recorded = True
    except (TypeError, ValueError):
        start = r.default if r.default is not None else (r.minimum + r.maximum) / 2
        recorded = False
    value = st.session_state.get(key, min(max(start, r.minimum), r.maximum))
    # A fresh slider sits at a default the annotator never chose. Until it is moved
    # (or the item already has a rating) the read-out says so and Save is disabled,
    # so an unconsidered default can never be recorded by a quick Enter.
    chosen = recorded or st.session_state.get(touched_key, False)
    word = r.word_for(value) if chosen else "move the slider to rate"
    num = _fmt(value, r.step) if chosen else "—"
    rec = ""
    if current == "UNSURE":
        rec = f'<span class="recorded">Recorded: <b>{html.escape(r.unsure_label or "unsure")}</b></span>'
    elif recorded:
        rec = f'<span class="recorded">Recorded: <b>{_fmt(start, r.step)}</b></span>'
    st.markdown(f'<div class="answer-head"><span class="label">Your rating</span>{rec}</div>'
                f'<div class="rating-readout"><span class="num{"" if chosen else " unset"}">{num}</span>'
                f'<span class="word">{html.escape(word)}</span></div>', unsafe_allow_html=True)
    value = st.slider("Rating", min_value=float(r.minimum), max_value=float(r.maximum),
                      value=float(min(max(start, r.minimum), r.maximum)), step=float(r.step),
                      key=key, label_visibility="collapsed",
                      on_change=lambda: st.session_state.__setitem__(touched_key, True))
    st.markdown(_marks_html(r), unsafe_allow_html=True)
    cols = st.columns([2, 1, 1] if r.unsure_label else [2, 1])
    with cols[0]:
        if st.button(f"Save {_fmt(value, r.step)}" if chosen else "Save", type="primary",
                     width="stretch", disabled=not chosen, help="Enter",
                     key=f"rating_save_{batch}_{qid}"):
            save(_fmt(value, r.step))
    if r.unsure_label:
        sel = "S" if current == "UNSURE" else ""
        with cols[1]:
            if st.button(r.unsure_label, width="stretch", help="Key 1",
                         key=f"verdict_1_xneu{sel}_{batch}_{qid}"):
                save("UNSURE")
    with cols[-1]:
        if st.button("Skip", width="stretch", help="Key S", key=f"verdict_skip_{batch}_{qid}"):
            st.session_state.query_idx = find_step(total, idx, labeled, True, skip)
            st.rerun()
