"""Tab 1 — Query Console: read-only Cypher execution against a project's graph."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from ..config import WorkspaceConfig
from ..storage import jsonify_complex_cells, load_benchmark
from ..graphdb import run_query, write_guard_reason


def render(cfg: WorkspaceConfig) -> None:
    st.subheader("Query console")
    st.caption(
        "Read-only execution environment. Write operations "
        "(CREATE, MERGE, SET, DELETE, …) are rejected before they are sent."
    )

    targets = cfg.graph_targets
    if not targets:
        st.info(
            "No project in this configuration declares a `graph_db:` block, so "
            "there is nothing to query. Annotation still works — it reads "
            "precomputed results from the gold cache."
        )
        return

    target_label = st.selectbox("Target graph database", list(targets.keys()))
    target = targets[target_label]
    if not target.password_available:
        st.warning(
            f"Environment variable `{target.password_env}` is not set, so this "
            f"connection will fail to authenticate. Export it and restart the app."
        )

    # Load a benchmark item's gold query into the editor, per project (each
    # project numbers its queries from ~0/1, so the project must be explicit).
    lc0, lc1, lc2, _ = st.columns([2, 2, 1, 3])
    with lc0:
        project_label = st.selectbox(
            "Benchmark",
            [p.name for p in cfg.projects if p.benchmark_csv is not None],
            key="console_project",
            help="Query ids are per-project; pick which project this id refers to.",
        )
    project = next(p for p in cfg.projects if p.name == project_label)
    with lc1:
        lookup_id = st.number_input(
            "Load by query id", min_value=0, step=1, key="console_lookup_id"
        )
    with lc2:
        st.markdown("<div style='height: 1.9rem'></div>", unsafe_allow_html=True)
        load_clicked = st.button("Load query", width="stretch")

    if load_clicked:
        master = load_benchmark(project)
        try:
            match = master[master["id"].astype(str) == str(int(lookup_id))]
        except Exception:
            match = master.iloc[0:0]
        if match.empty:
            st.session_state["_console_lookup_error"] = (
                f"No query with id {int(lookup_id)} in project '{project.name}'."
            )
        else:
            st.session_state["console_cypher"] = str(match.iloc[0]["gold_cypher"])
            st.session_state.pop("_console_lookup_error", None)
        st.rerun()

    if st.session_state.get("_console_lookup_error"):
        st.error(st.session_state["_console_lookup_error"])

    st.session_state.setdefault("console_cypher", "")
    cypher_in = st.text_area(
        "Cypher query",
        height=200,
        placeholder=project.cypher_placeholder or "MATCH (n) RETURN n LIMIT 10",
        key="console_cypher",
    )

    col1, _ = st.columns([1, 5])
    with col1:
        run_clicked = st.button("Execute", type="primary", width="stretch")

    if run_clicked and cypher_in and cypher_in.strip():
        reason = write_guard_reason(cypher_in)
        if reason:
            st.error(
                f"Query rejected: this environment is read-only, and the query "
                f"{reason}."
            )
        else:
            with st.spinner("Executing query…"):
                records, elapsed, error = run_query(
                    cypher_in, target, timeout=cfg.query_timeout_seconds
                )
            if error:
                st.error(f"Query execution failed ({elapsed} s)")
                st.code(error)
            elif records:
                st.success(
                    f"Execution complete — {len(records):,} rows returned in {elapsed} s"
                )
                st.dataframe(
                    jsonify_complex_cells(pd.DataFrame(records)),
                    width="stretch",
                )
            else:
                st.success(f"Execution complete in {elapsed} s")
                st.info("The query executed successfully and returned zero rows.")

    if project.schema_help:
        st.divider()
        with st.expander("Schema reference"):
            columns = st.columns(max(1, min(len(project.schema_help), 3)))
            for i, section in enumerate(project.schema_help):
                with columns[i % len(columns)]:
                    if section.title:
                        st.markdown(f"**{section.title}**")
                    st.markdown(section.body)
