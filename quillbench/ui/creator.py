"""The page where people write new benchmark items, and its progress view."""

from __future__ import annotations

import hashlib

import pandas as pd
import streamlit as st

from .. import benchmark, connections
from ..config import WorkspaceConfig


@st.cache_data(ttl=600, show_spinner="Reading the schema…")
def _schema(conn_id: str, _stamp: str) -> tuple[dict, str | None]:
    conn = connections.get(conn_id)
    if conn is None:
        return {}, "The database connection for this project was removed. Tell the study owner."
    return connections.schema(conn)


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


def _schema_slot(slot: benchmark.Slot, conn: connections.Connection | None, key: str) -> None:
    if slot.text:
        st.markdown(slot.text)
    if conn is None:
        st.warning("No database connection is set for this project.")
        return
    tables, err = _schema(conn.id, _conn_stamp(conn))
    if err:
        st.error(f"Could not read the schema: {err}")
        return
    if not tables:
        st.caption("The database has no tables.")
        return
    find = st.text_input("Find a table or column", key=f"{key}_find",
                         placeholder="Type to filter", label_visibility="collapsed")
    word = find.strip().lower()
    shown = {t: cols for t, cols in tables.items()
             if not word or word in t.lower() or any(word in c.lower() for c in cols)}
    st.caption(f"{len(shown)} of {len(tables)} {'labels and relationships' if conn.engine == 'neo4j' else 'tables'}")
    lines = [f"{t}({', '.join(cols)})" for t, cols in shown.items()]
    st.code("\n".join(lines) or "(nothing matches)", language=None, wrap_lines=True)


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
        return st.text_input(label, key=key, help=slot.help or None)
    if slot.type == "long_text":
        return st.text_area(label, key=key, help=slot.help or None, height=110)
    return None


def _filled(value) -> bool:
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return bool(value)
    if isinstance(value, float):
        return True
    return bool(str(value).strip())


def render(ws: WorkspaceConfig, me: str, prefix: str = "") -> None:
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
    c1, c2 = st.columns([1, 3])
    c1.metric("Added by you", mine["added"])
    note = st.session_state.pop(f"bm{prefix}_note_{ws.id}", None)
    with c2:
        if note and note[0] == "ok":
            st.success(note[1])
        elif note:
            st.error(note[1])
    if project.instructions:
        st.info(project.instructions)

    answers: dict = {}
    question = query = ""
    confirmed = False
    for slot in form.slots:
        skey = f"{key}_{slot.id}"
        with st.container(border=True):
            if slot.type in ("text", "schema", "examples", "result") and slot.label:
                st.markdown(f"**{slot.label}**")
            if slot.type == "text":
                st.markdown(slot.text)
            elif slot.type == "schema":
                _schema_slot(slot, conn, skey)
            elif slot.type == "examples":
                _examples_slot(slot, conn, skey, form.min_rows)
            elif slot.type == "question":
                question = st.text_area(slot.label or "Question", key=skey, height=90,
                                        help=slot.help or None)
            elif slot.type == "query":
                editor = st.container(key=f"qb_query_editor{prefix}_{ws.id}")
                query = editor.text_area(slot.label or "Query", key=skey, height=180,
                                     help=slot.help or None,
                                     placeholder="SELECT …" if (not conn or conn.language == "sql")
                                     else "MATCH …")
                if st.button("Run query", key=f"{skey}_run", disabled=conn is None or not query.strip(),
                             type="secondary"):
                    st.session_state[run_key] = {"query": query, "result": connections.run(
                        conn, query, timeout=ws.query_timeout_seconds, cap=ws.display_row_cap)}
            elif slot.type == "result":
                last = st.session_state.get(run_key)
                if not last:
                    st.caption("Run the query to see its result here.")
                elif last["query"] != query:
                    st.caption("The query changed since it last ran. Run it again.")
                else:
                    _show_result(last["result"], form.min_rows)
                confirmed = st.checkbox(slot.confirm, key=f"{skey}_ok")
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
    if not confirmed:
        missing.append("tick the result check box")
    for s in form.slots:
        if s.column and s.required and not _filled(answers.get(s.id)):
            missing.append(f"fill in '{s.label or s.id}'")

    if st.button("Add to benchmark", type="primary", key=f"{key}_add", disabled=bool(missing),
                 width="stretch"):
        clean = {k: (list(v) if isinstance(v, (list, tuple)) else ("" if v is None else v))
                 for k, v in answers.items()}
        outcome = benchmark.submit(ws, form, me, question, query, res.columns, res.rows, clean)
        if outcome.accepted:
            st.session_state[f"bm{prefix}_note_{ws.id}"] = ("ok", f"Added to the benchmark as {outcome.id}.")
            st.session_state[vkey] = v + 1
            st.session_state.pop(run_key, None)
        else:
            st.session_state[f"bm{prefix}_note_{ws.id}"] = (
                "no", f"Not added: this question is {outcome.similarity:.0%} similar to one already "
                      "in the benchmark. Write a different question.")
        st.rerun()
    if missing:
        st.caption("To add this entry: " + "; ".join(missing) + ".")


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
    m1, m2, m3 = st.columns(3)
    m1.metric("Entries in the benchmark", len(accepted))
    m2.metric("Not added (too similar)", len(rejected))
    m3.metric("People", len([p for p in counts if p]))
    if counts:
        st.dataframe(pd.DataFrame([{"Person": p, "Added": c["added"], "Not added": c["not_added"]}
                                   for p, c in sorted(counts.items())]),
                     width="stretch", hide_index=True)
    d1, d2 = st.columns(2)
    for col, path, label in ((d1, benchmark.accepted_path(ws), "benchmark.csv"),
                             (d2, benchmark.rejected_path(ws), "rejected.csv")):
        if path.exists():
            col.download_button(f"Download {label}", path.read_bytes(), file_name=f"{ws.id}_{label}",
                                key=f"bm_dl_{ws.id}_{label}", width="stretch")
    st.caption(f"Saved on the server in `{benchmark.data_dir(ws)}`")
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
