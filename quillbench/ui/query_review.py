"""
Annotation screen for query-review tasks: an item, its reference query and
precomputed results, and the task's answer buttons. Rating, choice and text
tasks use ``generic.py``.

Layout and interaction here are distilled from extended real annotator use;
several apparently odd choices are load-bearing and are commented at the
point where they matter. Read those comments before "simplifying" anything.
"""

from __future__ import annotations

import html
import time

import pandas as pd
import streamlit as st

from ..config import ConfigError, ProjectConfig, WorkspaceConfig
from .components import (
    answer_buttons,
    answer_head,
    done_card,
    item_nav,
    needs_note,
    toolbar,
)
from ..storage import (
    batch_path,
    blank_if_nan,
    find_step,
    gold_cache_missing_reason,
    labeled_mask,
    load_batch,
    load_gold_cache,
    load_progress,
    load_jury,
    get_progress_df,
    set_verdict,
)


def item_timer(batch: str, qid) -> float:
    """Seconds this item has been open. Restarts when the item changes."""
    key = (batch, str(qid))
    if st.session_state.get("_item_open_key") != key:
        st.session_state["_item_open_key"] = key
        st.session_state["_item_open_at"] = time.monotonic()
    return time.monotonic() - st.session_state["_item_open_at"]


def render(cfg: WorkspaceConfig, annotator: str, assignments: dict, active_batch: str,
           bar, next_batch: str | None = None) -> None:
    """``assignments`` is the workspace's ``{name: {role, batches}}`` view; ``bar`` is
    the work bar's columns (batch picker already in slot 0)."""
    if st.session_state.active_batch != active_batch:
        st.session_state.active_batch = active_batch
        st.session_state.query_idx = 0

    batch_df = load_batch(cfg, active_batch)
    if batch_df.empty:
        st.error(f"Batch file not found or empty: {batch_path(cfg, active_batch)}")
        return
    if "id" not in batch_df.columns:
        st.error(f"Batch '{active_batch}' has no 'id' column; it cannot be annotated.")
        return

    project = cfg.project_for_batch(active_batch)
    if project.task_spec.view == "adjudication":
        # The REFERENCE result is shown as context; the predicted query's result is
        # never shown and never computed. Only the comparison of the two would be
        # circular. See adjudication/GUIDELINES.md.
        try:
            gold_cache = load_gold_cache(project, active_batch)
        except ConfigError:
            gold_cache = {}
        df = get_progress_df(cfg, annotator, active_batch, batch_df)
        _adjudication_view(cfg, project, annotator, active_batch, df,
                           batch_df, assignments, gold_cache, bar, next_batch)
        return
    try:
        gold_cache = load_gold_cache(project, active_batch)
    except ConfigError as exc:
        # A malformed gold cache is an operator/data error, not something an
        # annotator can act on. Report it precisely (it names the bad id) and
        # stop, rather than rendering a batch that will crash on one item.
        st.error(f"Gold cache for this batch is malformed:\n\n{exc}")
        return
    missing_reason = gold_cache_missing_reason(project, active_batch)
    if missing_reason:
        st.warning(missing_reason)

    jury_df = (
        load_jury(project)
        if (project.jury is not None and not project.jury.hidden)
        else pd.DataFrame()
    )

    df = get_progress_df(cfg, annotator, active_batch, batch_df)
    total = len(df)
    labeled = labeled_mask(df, project)
    n_labeled = int(labeled.sum())
    skip_labeled = toolbar(bar, project, n_labeled, total, box_sizes=True)
    done_card(n_labeled, total, next_batch)

    if total == 0:
        st.info("This batch is empty.")
        return

    idx = min(max(st.session_state.query_idx, 0), total - 1)
    _item_panel(cfg, project, annotator, active_batch, df, batch_df, idx, total,
                labeled, skip_labeled, gold_cache, jury_df, "local_id" in df.columns)


# ──────────────────────────────────────────────────────────────────────────
def _item_panel(
    cfg, project, annotator, active_batch, df, batch_df, idx, total,
    labeled, skip_labeled, gold_cache, jury_df, has_local_id,
) -> None:
    st.session_state.setdefault("ui_cypher_h", 320)
    st.session_state.setdefault("ui_result_h", 400)
    st.session_state.setdefault("ui_note_h", 90)
    row = df.iloc[idx]
    qid = row["id"]
    # blank_if_nan, not str(): a batch CSV with a gap in local_id would
    # otherwise render the literal text "nan" as the item's identifier.
    local_id = (blank_if_nan(row["local_id"]) or idx + 1) if has_local_id else idx + 1
    current_verdict = blank_if_nan(row.get("human_verdict", ""))
    current_note = blank_if_nan(row.get("note", ""))
    current_paraphrase = blank_if_nan(row.get("paraphrase", ""))
    spec = project.task_spec
    seconds_open = item_timer(active_batch, qid)

    with st.container(key="quillbench_item"):
        item_nav(df, idx, total, labeled, skip_labeled, active_batch, project)

        badges = [f'<span class="meta-badge primary">Item {html.escape(str(local_id))}</span>']
        if spec.is_labeled(current_verdict):
            badges.append('<span class="meta-badge done">✓ answered</span>')
        for label, key in (("Difficulty", "difficulty"), ("Category", "category"),
                           ("Split", "split")):
            value = blank_if_nan(row.get(key, ""))
            if value:
                badges.append(f'<span class="meta-badge">{label}: {html.escape(value)}</span>')
        badges.append(f'<span class="meta-badge subtle">ID {html.escape(str(qid))}</span>')
        st.markdown("".join(badges), unsafe_allow_html=True)
        st.markdown(
            f'<div class="nl-question">{html.escape(blank_if_nan(row.get("nl_query", "")))}</div>',
            unsafe_allow_html=True,
        )

        # For gate review, the accusation travels with the item. An instruction sheet in a
        # separate document is a sheet nobody re-reads on item 400.
        if project.task in ("gate_review", "gate_calibration"):
            reason = blank_if_nan(row.get("gate_reason", ""))
            detail = blank_if_nan(row.get("gate_detail", ""))
            if reason:
                st.warning(f"**Why this item was flagged ({blank_if_nan(row.get('gate', ''))}):** "
                           f"{reason}" + (f"\n\n`{detail}`" if detail else ""))
            # Gate C's whole accusation is "this item shares its answer with another one". The
            # first round named the collision without showing the partner, and 44-47% of gate-C
            # labels came back CANT_TELL -- the highest of any gate. The partner travels with
            # the item now.
            partner_q = blank_if_nan(row.get("partner_nl_query", ""))
            if partner_q:
                st.info(
                    "**The other question that returns the same answer:**\n\n"
                    f"> {partner_q}\n\n"
                    f"`{blank_if_nan(row.get('partner_gold_cypher', ''))}`"
                )

        # The question reviewer is deliberately not shown the Cypher. A faithful implementation
        # of a bad question reads as a good question, which is how the first validation round
        # let ambiguous wording through: the reader saw the query, understood which reading was
        # meant, and stopped noticing that the English never said so.
        if spec.view == "pair":
            # Neutral A/B presentation. Nothing here may say which query is the model's,
            # and no execution result is shown: the protocol makes this a reading task,
            # and showing results would let the labeler infer the condition.
            ca, cb = st.columns(2)
            with ca:
                st.markdown('<div class="field-label">Query A</div>', unsafe_allow_html=True)
                with st.container(height=st.session_state.ui_cypher_h):
                    st.code(blank_if_nan(row.get("query_A", "")) or "—",
                            language="sql", wrap_lines=True)
            with cb:
                st.markdown('<div class="field-label">Query B</div>', unsafe_allow_html=True)
                with st.container(height=st.session_state.ui_cypher_h):
                    st.code(blank_if_nan(row.get("query_B", "")) or "—",
                            language="sql", wrap_lines=True)
            evidence = blank_if_nan(row.get("evidence", ""))
            if evidence:
                st.markdown('<div class="field-label">Evidence note — binding, part of the '
                            'question</div>', unsafe_allow_html=True)
                st.info(evidence)
            # Some batches ship a schema block per item; older batches have
            # no such column and are unaffected.
            schema = blank_if_nan(row.get("schema", ""))
            if schema:
                st.markdown('<div class="field-label">Schema of the tables these queries use</div>',
                            unsafe_allow_html=True)
                with st.container(height=220):
                    st.code(schema, language="text")
        elif not spec.shows_query:
            _contract_panel(row)
            _result_panel(cfg, gold_cache, qid)
        else:
            col_left, col_right = st.columns([3, 2])
            with col_left:
                _gold_query_panel(row)
                _contract_panel(row)
                _jury_panel(project, jury_df, qid)
            with col_right:
                _result_panel(cfg, gold_cache, qid)

        note_val = st.text_area(
            spec.note_label,
            value=current_note,
            placeholder=spec.note_placeholder,
            height=st.session_state.ui_note_h,
            key=f"note_{active_batch}_{qid}",
        )
        paraphrase_val = None
        if project.ask_paraphrase:
            with st.expander("Paraphrase the question (optional)",
                             expanded=bool(current_paraphrase)):
                paraphrase_val = st.text_area(
                    "Paraphrase of the NL question",
                    value=current_paraphrase,
                    placeholder="Rewrite the question above in your own words…",
                    height=st.session_state.ui_note_h,
                    key=f"paraphrase_{active_batch}_{qid}",
                    label_visibility="collapsed",
                )

    # One view, many tasks: the buttons come from the task definition, so the tasks stay
    # in step with each other. The bar stays on screen while the item scrolls.
    with st.container(key="quillbench_answerbar"):
        answer_head("Your verdict", spec, current_verdict)
        verdict = answer_buttons(spec, current_verdict, active_batch, qid)
    if verdict is None:
        return
    # Some labels are the ones the exercise exists to collect, so they must carry a
    # reason. A bare rejection tells us nothing and cannot be acted on.
    if verdict and needs_note(spec, verdict, note_val):
        return
    if verdict:
        set_verdict(cfg, annotator, active_batch, qid, verdict, note_val, batch_df,
                    paraphrase_val, seconds=seconds_open)
    st.session_state.query_idx = find_step(total, idx, labeled, True, skip_labeled)
    st.rerun()


def _contract_panel(row) -> None:
    """The three declared contracts, shown beside the item that has to honour them.

    Some items carry rules that are properties of the dataset, not of the question: annual
    and monthly rows are separate grains and must never be summed together; AIR and SEA are
    international aggregates and are not countries; an absent cell is a missing fact and not
    a zero. A reviewer who does not know these cannot tell a correct query from a wrong one,
    and asking them to remember all three across 260 items is how you get a rubber stamp.
    """
    fields = [
        ("Units", "unit_contract"),
        ("Who counts", "population_contract"),
        ("Missing vs zero", "missingness_contract"),
    ]
    present = [(label, blank_if_nan(row.get(key, ""))) for label, key in fields]
    present = [(label, val) for label, val in present if val]
    if not present:
        return
    with st.expander("Rules this item must follow", expanded=False):
        for label, val in present:
            st.markdown(f"**{label}** — {val}")
        flags = blank_if_nan(row.get("quality_flags", ""))
        if flags:
            st.caption(
                f"Automatic note: `{flags}`. This is a hint, not an accusation — the check is "
                "new and has been wrong before. Judge the item, not the flag."
            )


def _gold_query_panel(row) -> None:
    cypher_text = blank_if_nan(row.get("gold_cypher", ""))
    with st.expander("Reference (gold) query", expanded=True):
        if cypher_text:
            # Wrapped in a sized container rather than relying only on
            # st.code's own height= kwarg — more reliably resizable across
            # Streamlit versions, and shares the slider primitive with the
            # result panel below.
            with st.container(height=st.session_state.ui_cypher_h):
                st.code(cypher_text, language="cypher", wrap_lines=True)
        else:
            st.caption("Not available in the batch file.")


def _jury_panel(project, jury_df, qid) -> None:
    jury = project.jury
    if jury is None:
        return
    if jury.hidden:
        st.caption(jury.hidden_message)
        return
    if jury_df.empty:
        return
    matches = jury_df[jury_df[jury.id_column].astype(str) == str(qid)]
    if matches.empty:
        return
    record = matches.iloc[0]
    aggregate = blank_if_nan(record.get(jury.verdict_column, "")) or "—"
    with st.expander(jury.title):
        st.markdown(f"**Aggregate verdict:** {aggregate}")
        for judge in jury.judges:
            verdict = blank_if_nan(record.get(jury.verdict_col_for(judge), "")) or "—"
            st.markdown(f"- **{judge}**: {verdict}")
        reasons = [
            (judge, blank_if_nan(record.get(jury.reason_col_for(judge), "")))
            for judge in jury.judges
        ]
        reasons = [(j, r) for j, r in reasons if r]
        if reasons:
            st.divider()
            for judge, reason in reasons:
                st.markdown(f"**{judge} rationale:** {reason}")


def _result_panel(cfg: WorkspaceConfig, gold_cache: dict, qid) -> None:
    cached = gold_cache.get(str(qid), {})
    # Belt-and-braces: load_gold_cache already rejects non-dict entries, but
    # this panel is the one place a bad entry used to crash the UI, so it
    # degrades explicitly rather than raising AttributeError.
    if not isinstance(cached, dict):
        st.error(
            f"Gold cache entry for id '{qid}' is malformed "
            f"({type(cached).__name__}, expected an object). Regenerate the "
            f"gold cache for this batch."
        )
        return
    records = cached.get("records", []) or []
    elapsed = cached.get("elapsed")
    error = blank_if_nan(cached.get("error", ""))

    st.markdown('<div class="field-label">Execution result · precomputed</div>',
                unsafe_allow_html=True)
    if error:
        st.error(f"Execution error: {error}")
        return
    if not records:
        st.warning(
            "Zero rows returned. This may be valid for threshold or anti-join queries."
        )
        return

    caption = f"{len(records):,} rows"
    if elapsed:
        caption += f" · {elapsed} s"
    st.caption(caption)

    # st.table (plain server-rendered HTML) instead of st.dataframe: the
    # canvas-based interactive grid was found to trigger a React "Maximum
    # update depth" crash in certain navigation sequences in this tab.
    # st.table has no client-side resize/measurement logic at all, so that
    # whole bug class does not apply to it. Do not "fix" this back.
    #
    # But st.table has no row virtualization either: an unaggregated query
    # can return 100k+ rows, and rendering all of them as literal <tr>
    # elements builds a DOM large enough that the browser stops reliably
    # dispatching click events — verdict buttons then appear to do nothing.
    # So cap what is displayed. The true row count is shown above, which is
    # what matters for judging correctness (structure + representative rows).
    cap = cfg.display_row_cap
    shown = records[:cap]
    if len(records) > cap:
        st.caption(
            f"Showing first {cap:,} of {len(records):,} rows "
            f"(full result too large to render safely in-browser)."
        )
    with st.container(height=st.session_state.ui_result_h):
        st.table(pd.DataFrame(shown))


# ──────────────────────────────────────────────────────────────────────────
# Adjudication task. Separate from the verdict task above because it
# judges a different object: a PREDICTED query against the question, rather than a
# reference result against the question.
def _peer_labels(cfg, assignments, batch_name, me, project) -> pd.DataFrame:
    """Every other assigned annotator's current labels for this batch, id-indexed.

    Read fresh on each rerun (load_progress is mtime-keyed), so an adjudicator watching
    this page sees a label appear as soon as the annotator records it.
    """
    peers = [name for name, rec in assignments.items()
             if name != me and batch_name in (rec.get("batches") or [])
             and rec.get("role", "annotator") == "annotator"]
    cols = {}
    for name in peers:
        prog = load_progress(cfg, name, batch_name)
        if prog.empty or "human_verdict" not in prog.columns:
            # A column of NaN, not a missing column. Dropping it would make an item
            # labelled by one of two annotators look complete, and the adjudicator
            # would be told there was nothing waiting.
            cols[name] = pd.Series(dtype=object)
            continue
        ser = prog.set_index("id")["human_verdict"].astype(str).str.strip()
        cols[name] = ser[ser.apply(project.task_spec.is_labeled)]
    return pd.DataFrame(cols) if cols else pd.DataFrame()


def _adjudication_view(cfg, project, annotator, active_batch, df, batch_df,
                       assignments, gold_cache=None, bar=None, next_batch=None) -> None:
    gold_cache = gold_cache or {}
    all_labeled = labeled_mask(df, project)
    skip_labeled = toolbar(bar, project, int(all_labeled.sum()), len(df), box_sizes=True)
    # Peer labels are for ADJUDICATORS ONLY. Showing one annotator the other's label
    # would destroy the independence that makes the agreement statistic mean anything,
    # so this is gated on role and not on "are there any peers".
    is_adjudicator = assignments.get(annotator, {}).get("role", "") == "admin"
    peers = (_peer_labels(cfg, assignments, active_batch, annotator, project)
             if is_adjudicator else pd.DataFrame())
    if peers.empty or not len(peers.columns):
        is_adjudicator = False

    if is_adjudicator:
        # Adjudicator mode: the two annotators' labels are shown, and the default view
        # is the subset that actually needs a decision.
        # An item is adjudicable once ANY TWO annotators have labelled it -- not once
        # every assigned annotator has. Some batches carry a third annotator for a
        # separate purpose, and waiting on them would
        # hide real disagreements for weeks.
        n_labels = peers.notna().sum(axis=1)
        complete = n_labels >= 2
        unanimous = peers.apply(lambda r: r.dropna().nunique() == 1, axis=1)
        disagreeing = set(peers.index[complete & ~unanimous])
        pending = set(peers.index[n_labels == 1])
        choice = st.radio(
            "Show",
            ["Disagreements only", "Both labelled", "All items"],
            horizontal=True, key=f"adjfilter_{active_batch}",
            help="Updates live as annotators record labels.",
        )
        c1, c2, c3 = st.columns(3)
        c1.metric("Two or more labels", int(complete.sum()) if len(complete) else 0)
        c2.metric("Disagreements", len(disagreeing))
        c3.metric("Awaiting a second label", len(pending))
        if choice == "Disagreements only":
            df = df[df["id"].isin(disagreeing)]
        elif choice == "Both labelled":
            df = df[df["id"].isin(set(peers.index[complete]) if len(complete) else set())]
        df = df.reset_index(drop=True)
        if df.empty:
            if disagreeing:
                st.success("Nothing to adjudicate under the current filter.")
            elif pending:
                st.info(
                    f"No disagreements yet. {len(pending)} item(s) in this batch are "
                    f"labelled by some but not all annotators."
                )
            else:
                st.info("No annotator has finished an item in this batch yet.")
            return

    total = len(df)
    labeled = labeled_mask(df, project)
    n_labeled = int(labeled.sum())
    spec = project.task_spec
    if not is_adjudicator:
        done_card(n_labeled, total, next_batch)

    if total == 0:
        st.info("This batch is empty.")
        return

    idx = min(max(st.session_state.query_idx, 0), total - 1)
    st.session_state.setdefault("ui_cypher_h", 320)
    st.session_state.setdefault("ui_result_h", 400)
    st.session_state.setdefault("ui_note_h", 90)

    with st.container(key="quillbench_item"):
        item_nav(df, idx, total, labeled, skip_labeled, active_batch, project)

        row = df.iloc[idx]
        qid = row["id"]
        seconds_open = item_timer(active_batch, qid)
        badges = [
            f'<span class="meta-badge primary">Item {html.escape(str(blank_if_nan(row.get("local_id", idx + 1))))}</span>'
        ]
        for label, key in (("Benchmark", "benchmark"), ("Category", "category"),
                           ("Difficulty", "difficulty")):
            value = blank_if_nan(row.get(key, ""))
            if value:
                badges.append(f'<span class="meta-badge">{label}: {html.escape(value)}</span>')
        st.markdown("".join(badges), unsafe_allow_html=True)
        st.markdown(
            f'<div class="nl-question">{html.escape(blank_if_nan(row.get("nl_query", "")))}</div>',
            unsafe_allow_html=True,
        )

        c1, c2 = st.columns(2)
        with c1:
            st.markdown('<div class="field-label">Reference query — the benchmark\'s own</div>',
                        unsafe_allow_html=True)
            with st.container(height=st.session_state.ui_cypher_h):
                st.code(blank_if_nan(row.get("gold_cypher", "")) or "—",
                        language="cypher", wrap_lines=True)
        with c2:
            st.markdown('<div class="field-label">Predicted query — judge this one</div>',
                        unsafe_allow_html=True)
            with st.container(height=st.session_state.ui_cypher_h):
                st.code(blank_if_nan(row.get("pred_cypher", "")) or "—",
                        language="cypher", wrap_lines=True)

        # Row counts only, never the rows. Enough to see "this returned nothing";
        # not enough to decide the item by comparing outputs.
        gr, pr = blank_if_nan(row.get("gold_rows", "")), blank_if_nan(row.get("pred_rows", ""))
        status = blank_if_nan(row.get("pred_status", ""))
        st.caption(
            f"Rows returned — reference: {gr or '—'} · prediction: {pr or '—'}"
            f"{' · ' + status if status else ''}"
        )

        cached = gold_cache.get(str(qid)) or {}
        with st.expander(
            "Reference query's result — context only, NOT the criterion", expanded=False
        ):
            st.caption(
                "What the data looks like, so you can judge whether the prediction is "
                "right in general or only on this database. The predicted query's own "
                "result is deliberately not shown: deciding by whether the two agree is "
                "exactly what this study exists to avoid."
            )
            err = blank_if_nan(cached.get("error", ""))
            records = cached.get("records") or []
            if err:
                st.error(f"Execution error: {err}")
            elif not records:
                st.warning("Zero rows. This can be correct for threshold or anti-join "
                           "questions.")
            else:
                total_rows = cached.get("total_rows", len(records))
                if total_rows > len(records):
                    st.caption(f"Showing first {len(records)} of {total_rows:,} rows.")
                with st.container(height=st.session_state.ui_result_h):
                    st.table(pd.DataFrame(records))

        raw = blank_if_nan(row.get("raw_output", ""))
        if raw:
            with st.expander("Raw model output (context; the query above was extracted from it)"):
                st.text(raw[:8000])

        if is_adjudicator and qid in peers.index:
            marks = []
            for name in peers.columns:
                val = peers.loc[qid, name]
                val = "" if pd.isna(val) else str(val)
                marks.append(f"{spec.glyph_for(val)} **{name}**: {val or '—'}")
            st.markdown("Annotator labels — " + " · ".join(marks))

        current = blank_if_nan(row.get("human_verdict", ""))
        note_val = st.text_area(
            spec.note_label,
            value=blank_if_nan(row.get("note", "")),
            placeholder=spec.note_placeholder,
            height=st.session_state.ui_note_h,
            key=f"adjnote_{active_batch}_{qid}",
        )

    with st.container(key="quillbench_answerbar"):
        answer_head("Your label", spec, current)
        verdict = answer_buttons(spec, current, active_batch, qid)
    if verdict is None:
        return
    if verdict and needs_note(spec, verdict, note_val):
        return
    if verdict:
        set_verdict(cfg, annotator, active_batch, qid, verdict, note_val, batch_df,
                    seconds=seconds_open)
    st.session_state.query_idx = find_step(total, idx, labeled, True, skip_labeled)
    st.rerun()
