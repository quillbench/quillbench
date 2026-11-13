"""The page where people write new benchmark items, and its progress view."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from .. import benchmark, categories, connections, schema_info
from ..config import WorkspaceConfig
from .session import client_info


@st.cache_data(ttl=600, show_spinner="Reading the database structure…")
def _describe(conn_id: str, _stamp: str) -> tuple[dict, str | None]:
    conn = connections.get(conn_id)
    if conn is None:
        return {}, "The database connection for this project was removed. Tell the study owner."
    return schema_info.describe(conn)


@st.cache_data(ttl=600, show_spinner="Reading example rows…")
def _samples(conn_id: str, _stamp: str, table: str) -> connections.QueryResult:
    conn = connections.get(conn_id)
    tables, _ = schema_info.describe(conn)
    return connections.run(conn, schema_info.sample_query(conn, tables[table], 10), timeout=30, cap=10)


def _conn_stamp(conn: connections.Connection | None) -> str:
    """Changes when the admin edits the connection, so the cached schema is dropped."""
    if conn is None:
        return ""
    return hashlib.sha256(repr(conn).encode()).hexdigest()[:12]


def _table(columns: list, rows: list) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=columns if columns else None)


def _show_result(res: connections.QueryResult, min_rows: int) -> None:
    if not res.ok:
        st.error(res.error)
        return
    st.caption(f"{len(res.rows)}{'+' if res.truncated else ''} row(s) in {res.elapsed:.2f} s"
               + (" · showing the first rows only" if res.truncated else ""))
    if res.rows:
        st.dataframe(_table(res.columns, res.rows), width="stretch", hide_index=True,
                     height=min(38 + 35 * len(res.rows), 360))
    elif min_rows:
        st.warning("The query returned no rows. Entries need at least "
                   f"{min_rows} row{'s' if min_rows > 1 else ''} in the result.")


def _cell(v) -> str:
    text = "" if v is None else str(v)
    return text if len(text) <= 60 else text[:57] + "…"


def _compact(tables: dict) -> str:
    """Every table and its columns with types, as plain text people can copy (e.g. into a chatbot)."""
    lines = []
    for n, tb in tables.items():
        cols = ", ".join(f"{c.name} {c.type}".strip() + (f" [{c.key}]" if c.key else "") for c in tb.columns)
        extra = f"  -- {tb.comment}" if tb.comment and not tb.comment.startswith("public.") and tb.comment != n else ""
        lines.append(f"{n}({cols}){extra}")
    return "\n".join(lines)


def schema_page(ws: WorkspaceConfig) -> None:
    """The Schema tab: a copyable overview, then one table at a time with its first rows."""
    form = benchmark.load_form(ws)
    conn = connections.get(form.connection)
    slot = form.slot("schema") or benchmark.Slot("schema", "schema")
    if conn is None:
        st.warning("No database connection is set for this project.")
        return
    tables, err = _describe(conn.id, _conn_stamp(conn))
    if err:
        st.error(f"Could not read the database structure: {err}")
        return
    if slot.tables:
        tables = {n: tb for n, tb in tables.items() if n in slot.tables}
    if not tables:
        st.caption("The database has no tables.")
        return
    if slot.text:
        st.markdown(slot.text)
    graph = conn.engine == "neo4j"
    noun = "label or relationship" if graph else "table"

    st.markdown(f"**Look at one {noun}**")

    def label(n: str) -> str:
        tb = tables[n]
        desc = f" — {tb.comment}" if tb.comment and not tb.comment.startswith("public.") and tb.comment != n else ""
        return f"{n}{desc}"

    pick = st.selectbox(f"Choose a {noun}", list(tables), index=None, format_func=label,
                        placeholder=f"Choose a {noun} (type to search)", key=f"schema_pick_{ws.id}")
    if pick is not None:
        _one_table(conn, slot, tables, pick, graph)

    with st.expander("All tables at a glance (copyable)"):
        st.caption("Every table with its columns and types. Use the copy button on the right of the box "
                   "to paste it elsewhere, e.g. into an AI assistant, while working out a query.")
        st.code(_compact(tables), language=None, wrap_lines=True)


def _one_table(conn: connections.Connection, slot: benchmark.Slot, tables: dict, pick: str,
               graph: bool) -> None:
    tb = tables[pick]
    facts = [f"{len(tb.columns)} {'properties' if graph else 'columns'}"]
    if tb.rows:
        facts.append(f"about {tb.rows:,} rows")
    if tb.parts:
        facts.append(f"{len(tb.parts)} parts")
    st.markdown(f"#### {pick}")
    st.caption(" · ".join(facts))
    note = slot.notes.get(pick, "")
    if note:
        st.info(note)
    if tb.comment and tb.comment != pick and not tb.comment.startswith("public."):
        st.caption(f"Description in the database: {tb.comment}")
    if tb.parts:
        if tb.parent:
            st.caption(f"Stored in {len(tb.parts)} parts with the same columns: {tb.parts[0]} … "
                       f"{tb.parts[-1]}. Query **{pick}** to use them all, or one part for a single period.")
        else:
            st.caption(f"One table per period, all with the same columns: {', '.join(tb.parts)}. "
                       "Query the one you need, or combine them with UNION ALL.")
    frame = pd.DataFrame([{"Column" if not graph else "Property": c.name, "Type": c.type,
                           "Key": c.key, "Description": c.comment} for c in tb.columns])
    for col in ("Key", "Description"):
        if col in frame and not frame[col].astype(bool).any():
            frame = frame.drop(columns=col)
    st.table(frame.set_index(frame.columns[0]))
    if slot.samples:
        st.markdown("**First 10 rows**")
        res = _samples(conn.id, _conn_stamp(conn), pick)
        if not res.ok:
            st.error(res.error)
        elif not res.rows:
            st.caption("This table is empty.")
        else:
            st.dataframe(pd.DataFrame([[_cell(v) for v in r] for r in res.rows], columns=res.columns),
                         width="stretch", hide_index=True)
    if not graph:
        shared = [(c, ts) for c, ts in schema_info.shared_columns(tables, limit=500) if pick in ts]
        if shared:
            with st.expander("Other tables with the same column names (ways to join)"):
                st.table(pd.DataFrame([{"Column": c, "Also in": ", ".join(x for x in ts if x != pick)}
                                       for c, ts in shared]).set_index("Column"))


def _examples_slot(slot: benchmark.Slot, conn: connections.Connection | None, key: str,
                   min_rows: int) -> None:
    lang = conn.language if conn else "sql"
    for i, ex in enumerate(slot.examples):
        with st.expander(f"Example {i + 1}: {ex.get('question', '')}", expanded=i == 0):
            st.markdown(f"**Question:** {ex.get('question', '')}")
            st.code(ex.get("query", ""), language=lang)
            if ex.get("note"):
                st.caption(ex["note"])
            if conn is not None and ex.get("query") and st.button(
                    "Run this example", key=f"{key}_run_{i}"):
                _show_result(connections.run(conn, ex["query"]), 0)


def _chips(cats: list[str], form: benchmark.Form, is_admin: bool = False) -> None:
    """Complexity and points of the query; admins also see the categories behind them."""
    if not cats:
        return
    worth, top = form.score(cats)
    level = categories.complexity(cats)
    st.markdown(f"**Complexity: {level}** · {worth} points if added")
    if is_admin:
        chips = " ".join(f":blue-badge[{categories.label(c)}]" for c in cats)
        st.caption(f"Admins only: from *{categories.label(top)}*. Categories found: ")
        st.markdown(chips)


def _similarity_note(ws: WorkspaceConfig, form: benchmark.Form, question: str, me: str,
                     is_admin: bool = False) -> None:
    """How close the question is to the nearest accepted one, as a pop-up."""
    best = benchmark.nearest(question, benchmark.read_accepted(ws), 1)
    if not best or best[0][0] == 0:
        st.toast("Nothing similar in the benchmark yet.", icon="✅", duration="long")
        return
    score, row = best[0]
    own = row.get("author") == me
    who = (f"your entry “{row.get('question', '')}”" if own or is_admin else "a question already in the benchmark")
    if score >= form.threshold:
        st.toast(f"{score:.0%} similar to {who}. The limit is {form.threshold:.0%}: this would not be "
                 "added. Ask about something different.", icon="⚠️", duration="long")
    else:
        st.toast(f"Closest is {score:.0%} similar ({who}). The limit is {form.threshold:.0%}: "
                 "this can be added.", icon="✅", duration="long")


# Longest text one box takes: enough for any real question or query, and it keeps a
# pasted file from going into the shared CSV files.
MAX_SHORT, MAX_LONG, MAX_QUERY = 2_000, 5_000, 20_000


def _input(slot: benchmark.Slot, key: str):
    label = slot.label or slot.id
    if slot.type == "choice":
        return st.radio(label, slot.options, index=None, horizontal=True, key=key,
                        help=slot.help or None)
    if slot.type == "dropdown":
        return st.selectbox(label, slot.options, index=None, key=key, help=slot.help or None,
                            placeholder="Choose one")
    if slot.type == "multi":
        return st.multiselect(label, slot.options, key=key, help=slot.help or None)
    if slot.type == "rating":
        return st.slider(label, float(slot.minimum), float(slot.maximum),
                         value=float(slot.minimum), step=float(slot.step), key=key,
                         help=slot.help or None)
    if slot.type == "short_text":
        return st.text_input(label, key=key, help=slot.help or None, max_chars=MAX_SHORT)
    if slot.type == "long_text":
        return st.text_area(label, key=key, help=slot.help or None, height=110, max_chars=MAX_LONG)
    return None


def _filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return bool(value)
    if isinstance(value, float):
        return True
    return bool(str(value).strip())


def render(ws: WorkspaceConfig, me: str, prefix: str = "", is_admin: bool = False) -> None:
    form = benchmark.load_form(ws)
    problems = benchmark.check_form(form)
    if problems:
        st.warning("This project's form is not ready yet. The study owner is still setting it up.")
        return
    conn = connections.get(form.connection)
    project = ws.projects[0]

    # Bumping the version after a successful add gives the person empty fields.
    vkey = f"bm{prefix}_version_{ws.id}"
    v = st.session_state.setdefault(vkey, 0)
    key = f"bm{prefix}_{ws.id}_{v}"
    run_key = f"bm{prefix}_run_{ws.id}"

    mine = benchmark.counts(ws).get(me, {"added": 0, "not_added": 0})
    # The outcome of the last Add, as a pop-up that fades away.
    note = st.session_state.pop(f"bm{prefix}_note_{ws.id}", None)
    if note:
        st.toast(note[1], icon="✅" if note[0] == "ok" else "⚠️", duration="long")
    c1, c2 = st.columns([1, 3], vertical_alignment="center")
    c1.metric("Added by you", mine["added"])
    c2.caption("The tables and columns are on the **Schema** tab.")
    if project.instructions:
        st.info(project.instructions)

    language = conn.language if conn else "sql"
    answers: dict = {}
    question = query = ""
    verdict = None
    result_slot = form.slot("result")
    for slot in form.slots:
        if slot.type == "schema":
            continue        # shown on its own tab
        skey = f"{key}_{slot.id}"
        with st.container(border=True):
            if slot.type in ("text", "examples", "result") and slot.label:
                st.markdown(f"**{slot.label}**")
            if slot.type == "text":
                st.markdown(slot.text)
            elif slot.type == "examples":
                _examples_slot(slot, conn, skey, form.min_rows)
            elif slot.type == "question":
                question = st.text_area(slot.label or "Question", key=skey, height=90,
                                        help=slot.help or None, max_chars=MAX_SHORT)
                if st.button("Check similarity", key=f"{skey}_sim", disabled=not question.strip(),
                             help="Compares your question with every question already in the benchmark."):
                    _similarity_note(ws, form, question, me, is_admin)
            elif slot.type == "query":
                editor = st.container(key=f"qb_query_editor{prefix}_{ws.id}")
                query = editor.text_area(slot.label or "Query", key=skey, height=180,
                                     help=slot.help or None, max_chars=MAX_QUERY,
                                     placeholder="SELECT …" if (not conn or conn.language == "sql")
                                     else "MATCH …")
                if st.button("Run query", key=f"{skey}_run", disabled=conn is None or not query.strip(),
                             type="secondary"):
                    st.session_state[run_key] = {"query": query, "result": connections.run_shared(
                        conn, query, me, timeout=ws.query_timeout_seconds, cap=ws.display_row_cap)}
            elif slot.type == "result":
                last = st.session_state.get(run_key)
                if not last:
                    st.caption("Run the query to see its result here.")
                elif last["query"] != query:
                    st.caption("The query changed since it last ran. Run it again.")
                else:
                    _show_result(last["result"], form.min_rows)
                    if last["result"].ok:
                        _chips(categories.detect(query, language), form, is_admin)
                if slot.ask_verdict:
                    verdict = st.radio(slot.confirm or "Is the result right?", slot.verdicts, index=None,
                                       horizontal=True, key=f"{skey}_verdict", help=slot.help or None)
            else:
                answers[slot.id] = _input(slot, skey)

    # Everything that must be true before the entry can be added.
    last = st.session_state.get(run_key)
    fresh = bool(last) and last["query"] == query and bool(query.strip())
    res = last["result"] if fresh else None
    missing = []
    if not question.strip():
        missing.append("write the question")
    if not query.strip():
        missing.append("write the query")
    elif not fresh:
        missing.append("run the query")
    elif not res.ok:
        missing.append("fix the query error")
    elif len(res.rows) < form.min_rows:
        missing.append(f"get at least {form.min_rows} row(s) in the result")
    if not result_slot.ask_verdict:
        pass
    elif verdict is None:
        missing.append("choose " + " / ".join(result_slot.verdicts))
    elif verdict not in result_slot.accept:
        missing.append(f"entries marked '{verdict}' cannot be added; fix the query or choose another answer")
    for s in form.slots:
        if s.column and s.required and not _filled(answers.get(s.id)):
            missing.append(f"fill in '{s.label or s.id}'")

    if st.button("Add to benchmark", type="primary", key=f"{key}_add", disabled=bool(missing),
                 width="stretch"):
        clean = {k: (list(v) if isinstance(v, (list, tuple)) else ("" if v is None else v))
                 for k, v in answers.items()}
        outcome = benchmark.submit(ws, form, me, question, query, res.columns, res.rows, clean,
                                   verdict=verdict or "", language=language, client=client_info())
        if outcome.accepted:
            worth, _ = form.score(categories.detect(query, language))
            pts = worth if (not result_slot.ask_verdict or verdict in result_slot.scored) else 0
            st.session_state[f"bm{prefix}_note_{ws.id}"] = (
                "ok", f"Added as {outcome.id}: {categories.complexity(categories.detect(query, language))} "
                      f"query, +{pts} points.")
            st.session_state[vkey] = v + 1
            st.session_state.pop(run_key, None)
        else:
            st.session_state[f"bm{prefix}_note_{ws.id}"] = (
                "no", f"Not added: this question is {outcome.similarity:.0%} similar to one already "
                      "in the benchmark. Write a different question.")
        st.rerun()
    if missing:
        st.caption("To add this entry: " + "; ".join(missing) + ".")


def _profile_view(form: benchmark.Form, p: benchmark.Profile, language: str, is_admin: bool) -> None:
    """One person's scores. How they are worked out (categories, points per category) is for admins."""
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(f"Points (goal {form.min_total})" if form.min_total else "Points", p.total)
    m2.metric("Entries added", p.added)
    m3.metric("Not added (too similar)", p.not_added)
    missing = p.missing(form)
    if form.min_total or form.targets:
        m4.metric("Targets left", "✓" if p.done(form) else len(missing) + (p.total < form.min_total))
    if form.min_total:
        st.progress(min(p.total / form.min_total, 1.0),
                    text=f"Overall: {p.total} / {form.min_total} points")

    if not is_admin:
        _student_rest(form, p, missing)
        return

    st.markdown("**Points by category**")
    rows = []
    for c in categories.for_language(language):
        got = p.per_category.get(c.id, {"entries": 0, "points": 0})
        need = form.targets.get(c.id, 0)
        if not got["entries"] and not need:
            continue
        rows.append({"Category": c.label, "Entries": got["entries"], "Points": got["points"],
                     "Minimum": str(need) if need else "",
                     "Progress": min(got["points"] / need, 1.0) if need else None,
                     "Status": ("✓ met" if got["points"] >= need else f"{need - got['points']} more")
                     if need else ""})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                     column_config={"Progress": st.column_config.ProgressColumn(
                         "Progress", min_value=0.0, max_value=1.0, format="percent")})
    else:
        st.caption("No entries yet.")
    if missing:
        st.info("Still needed: " + "; ".join(f"{categories.label(c)} ({n} pt)" for c, n in missing.items())
                + ". Try a question that needs these.")
    if p.levels:
        order = categories.LEVEL_ORDER
        st.caption("Complexity of your entries: " + ", ".join(
            f"{n} {p.levels[n]}" for n in order if n in p.levels))
    if p.verdicts and form.slot("result").ask_verdict:
        st.caption("Verdicts: " + ", ".join(f"{v} {n}" for v, n in sorted(p.verdicts.items())))
    if p.entries:
        with st.expander(f"Entries ({len(p.entries)})"):
            st.dataframe(pd.DataFrame([{"Id": e["id"], "Question": e.get("question", ""),
                                        "Complexity": e["_level"],
                                        "Categories": ", ".join(categories.label(c) for c in e["_categories"]),
                                        "Their verdict": e.get("verdict") or "—", "Points": e["_points"]}
                                       for e in reversed(p.entries)]),
                         width="stretch", hide_index=True)


def _student_rest(form: benchmark.Form, p: benchmark.Profile, missing: dict) -> None:
    """What a student sees below the totals: their goals and their entries, not the scoring rules."""
    if missing:
        st.info("Still needed: " + ", ".join(categories.label(c) for c in missing)
                + ". Try questions that need these.")
    elif p.done(form) and (form.min_total or form.targets):
        st.success("You have met all your targets. Well done! More entries still count.")
    if p.levels:
        st.caption("Complexity of your entries: " + ", ".join(
            f"{n} {p.levels[n]}" for n in categories.LEVEL_ORDER if n in p.levels))
    asked = form.slot("result").ask_verdict
    if not p.entries:
        st.caption("No entries yet.")
        return
    st.markdown(f"**Your entries ({len(p.entries)})**")
    st.dataframe(pd.DataFrame([{"Id": e["id"], "Question": e.get("question", ""),
                                "Complexity": e["_level"],
                                **({"Your verdict": e.get("verdict") or "—"} if asked else {}),
                                "Points": e["_points"]}
                               for e in reversed(p.entries)]),
                 width="stretch", hide_index=True)


def profile(ws: WorkspaceConfig, me: str, is_admin: bool) -> None:
    """Profile tab: your own performance; admins can look at anyone's."""
    form = benchmark.load_form(ws)
    language = benchmark.language_of(form)
    profiles = benchmark.profiles(ws, form)
    who = me
    if is_admin:
        people = sorted(set(form.participants) | {p for p in profiles if p})
        if not people:
            st.caption("Nobody has written entries yet.")
            return
        who = st.selectbox("Person", people, index=people.index(me) if me in people else 0,
                           key=f"bm_profile_who_{ws.id}")
    _profile_view(form, profiles.get(who, benchmark.Profile(who)), language, is_admin)
    if not is_admin:
        return
    with st.expander("How points and complexity work (admins only)"):
        st.markdown("The system reads each query and finds its categories (join, group by, "
                    "average, …). An entry earns the points of its hardest category only, "
                    "when it is marked " + " or ".join(f"**{v}**" for v in form.slot("result").scored) + ".")
        st.dataframe(pd.DataFrame([{"Category": c.label, "Points": form.points_for(c.id),
                                    "Minimum per person": str(form.targets.get(c.id) or ""),
                                    "Example": c.hint}
                                   for c in categories.for_language(language)]),
                     width="stretch", hide_index=True)
        st.caption("Complexity is set by the system from the hardest category in the query: "
                   + ", ".join(f"{c.label} = {categories.LEVELS.get(c.points, categories.TOP_LEVEL)}"
                               for c in categories.for_language(language)) + ". "
                   "In the category table, a category's points show what you have used it for; "
                   "your total counts each entry once.")


def _similarity_checker(ws: WorkspaceConfig, form: benchmark.Form, accepted: list[dict]) -> None:
    """Admins: type any question and see the closest accepted ones, with scores."""
    with st.expander("Similarity checker"):
        st.caption(f"Type any question to see the closest questions in the benchmark. Entries "
                   f"{form.threshold:.0%} or more similar are not added.")
        q = st.text_input("Question", key=f"bm_simcheck_{ws.id}")
        if q.strip():
            rows = [{"Similarity": f"{s:.0%}", "Would block": "yes" if s >= form.threshold else "",
                     "Id": r.get("id", ""), "Person": r.get("author", ""), "Question": r.get("question", "")}
                    for s, r in benchmark.nearest(q, accepted, 10)]
            if rows:
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            else:
                st.caption("The benchmark is empty.")
        near = sorted((r for r in accepted if r.get("matched_id")),
                      key=lambda r: float(r.get("similarity") or 0), reverse=True)[:20]
        if near:
            by_id = {r["id"]: r.get("question", "") for r in accepted}
            st.markdown("**Closest pairs that were added** (just under the limit)")
            st.dataframe(pd.DataFrame([{"Similarity": f"{float(r['similarity']):.0%}", "Id": r["id"],
                                        "Question": r.get("question", ""), "Closest": r["matched_id"],
                                        "Closest question": by_id.get(r["matched_id"], "")}
                                       for r in near]), width="stretch", hide_index=True)


def progress(ws: WorkspaceConfig, me: str, is_admin: bool) -> None:
    """Counts per person; admins also see and download both files."""
    c0, c1 = st.columns([4, 1])
    c0.subheader("Progress")
    if c1.button("Refresh", width="stretch", key=f"bm_refresh_{ws.id}"):
        st.rerun()
    counts = benchmark.counts(ws)
    if not is_admin:
        mine = counts.get(me, {"added": 0})
        st.metric("Entries you added", mine["added"])
        return
    accepted, rejected = benchmark.read_accepted(ws), benchmark.read_rejected(ws)
    form = benchmark.load_form(ws)
    profiles = benchmark.profiles(ws, form)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Entries in the benchmark", len(accepted))
    m2.metric("Not added (too similar)", len(rejected))
    m3.metric("People", len([p for p in profiles if p]))
    if form.min_total or form.targets:
        m4.metric("Met all targets", sum(p.done(form) for p in profiles.values() if p.person))
    people = sorted(set(form.participants) | {p for p in profiles if p})
    if people:
        st.dataframe(pd.DataFrame([{"Person": n, "Added": profiles.get(n, benchmark.Profile(n)).added,
                                    "Not added": profiles.get(n, benchmark.Profile(n)).not_added,
                                    "Points": profiles.get(n, benchmark.Profile(n)).total,
                                    "Targets met": "✓" if profiles.get(n, benchmark.Profile(n)).done(form)
                                    else ", ".join(categories.label(c) for c in
                                                   profiles.get(n, benchmark.Profile(n)).missing(form))
                                    or f"{form.min_total} pt total"}
                                   for n in people]),
                     width="stretch", hide_index=True)
    totals = benchmark.category_totals(ws, form)
    if totals:
        st.markdown("**Entries per category** (the whole benchmark)")
        st.bar_chart(pd.DataFrame({"Entries": {categories.label(c): n for c, n in totals.items()}}),
                     horizontal=True, height=min(60 + 28 * len(totals), 600))
    d1, d2 = st.columns(2)
    for col, path, label in ((d1, benchmark.accepted_path(ws), "benchmark.csv"),
                             (d2, benchmark.rejected_path(ws), "rejected.csv")):
        if path.exists():
            col.download_button(f"Download {label}", path.read_bytes(), file_name=f"{ws.id}_{label}",
                                key=f"bm_dl_{ws.id}_{label}", width="stretch")
    if benchmark.students_dir(ws).exists():
        st.download_button("Download every person's file (zip: each attempt with time, IP and browser)",
                           benchmark.students_zip(ws), file_name=f"{ws.id}_students.zip",
                           mime="application/zip", key=f"bm_dl_{ws.id}_students", width="stretch")
    st.caption(f"Saved on the server in `{benchmark.data_dir(ws)}`; one file per person in "
               f"`{benchmark.STUDENTS}/`.")
    _similarity_checker(ws, form, accepted)
    if rejected:
        st.markdown("**Recently not added**")
        by_id = {r["id"]: r.get("question", "") for r in accepted}
        st.dataframe(pd.DataFrame([{"Id": r["id"], "Person": r.get("author", ""),
                                    "Question": r.get("question", ""),
                                    "Similarity": f"{float(r.get('similarity') or 0):.0%}",
                                    "Matched": r.get("matched_id", ""),
                                    "Matched question": by_id.get(r.get("matched_id", ""), "")}
                                   for r in rejected[-50:][::-1]]),
                     width="stretch", hide_index=True)
