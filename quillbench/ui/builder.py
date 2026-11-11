"""Admin screens to create a project and add to one, with no files to edit."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import accounts, benchmark, builder, connections
from ..config import ConfigError, WorkspaceConfig
from ..storage import batch_status, list_batches

AUTO_ID = "(number the rows for me)"


def _people() -> list[str]:
    return sorted(u for u, r in accounts.load_users().items() if r.get("active", True))


def _upload(key: str) -> pd.DataFrame | None:
    file = st.file_uploader("CSV file", type=["csv"], key=key)
    if file is None:
        return None
    try:
        table = builder.read_table(file)
    except (ValueError, pd.errors.ParserError, UnicodeDecodeError) as exc:
        st.error(f"Could not read that file: {exc}")
        return None
    st.caption(f"{len(table)} rows, {len(table.columns)} columns")
    st.dataframe(table.head(5), width="stretch", hide_index=True)
    return table


def _split_controls(table: pd.DataFrame, key: str) -> dict:
    st.markdown("**Split into batches and assign**")
    c1, c2 = st.columns(2)
    size = c1.number_input("Items per batch (0 = one batch)", 0, len(table), 0, key=f"{key}_size")
    per = c2.number_input("Annotators per batch", 1, 20, 1, key=f"{key}_per",
                          help="Above 1, each batch goes to several people, for agreement scores.")
    shuffle = st.checkbox("Shuffle items first", key=f"{key}_shuffle")
    people = st.multiselect("Annotators (optional, you can assign later)", _people(), key=f"{key}_people",
                            help="Batches are dealt to them in turn. Add people in the People tab.")
    batches = len(builder.split(table, int(size)))
    st.caption(f"This makes {batches} batch(es).")
    return {"batch_size": int(size), "shuffle": shuffle, "people": people, "per_batch": int(per)}


KINDS = {"annotate": "Annotate items I already have (upload a CSV)",
         "create": "Build a new benchmark: people write questions and queries"}


def _create(me: str) -> None:
    # Bumping the version gives the next project empty fields.
    v = st.session_state.get("new_version", 0)
    kind = st.radio("What is this project for?", list(KINDS), format_func=KINDS.get,
                    key=f"new_kind_{v}")
    name = st.text_input("Project name", key=f"new_name_{v}")
    if kind == "create":
        _create_benchmark(me, name, v)
        return
    task = st.selectbox("What annotators do", list(builder.TASKS), key=f"new_task_{v}",
                        format_func=builder.TASKS.get)
    labels, scale = None, None
    if task == "choice":
        labels = builder.clean_labels(st.text_area("Label buttons (one per line)", key=f"new_labels_{v}"))
    elif task == "rating":
        c1, c2, c3 = st.columns(3)
        scale = {"min": c1.number_input("Lowest", value=0.0, key=f"new_min_{v}"),
                 "max": c2.number_input("Highest", value=1.0, key=f"new_max_{v}"),
                 "step": c3.number_input("Step", value=0.1, min_value=0.001, key=f"new_step_{v}")}
    instructions = st.text_area("Instructions for annotators", key=f"new_instructions_{v}")
    st.caption("Upload CSV files after creating the project, under Admin → Projects.")
    if st.button("Create project", type="primary", key=f"new_create_{v}"):
        try:
            ws = builder.create_project(name, task, instructions, None, [], me,
                                        labels=labels, scale=scale)
        except (ValueError, ConfigError, OSError) as exc:
            st.error(str(exc))
            return
        st.cache_resource.clear()
        st.session_state.new_version = v + 1
        st.session_state.manage_project = ws.id
        st.rerun()


def _create_benchmark(me: str, name: str, v: int) -> None:
    conns = connections.load_all()
    if not conns:
        st.info("First add the database people will query, under Admin → Connections.")
    conn = st.selectbox("Database connection", list(conns), index=None, key=f"new_conn_{v}",
                        format_func=lambda c: f"{conns[c].name} ({connections.ENGINES[conns[c].engine]})",
                        placeholder="Choose a connection")
    people = st.multiselect("People who write entries (you can change this later)", _people(),
                            key=f"new_bm_people_{v}")
    instructions = st.text_area("Instructions shown at the top of the page", key=f"new_bm_instr_{v}")
    st.caption("The project starts with a text block, the schema, and the question, query and "
               "result slots. Add or reorder slots after creating it.")
    if st.button("Create project", type="primary", key=f"new_bm_create_{v}", disabled=not conn):
        try:
            ws = builder.create_benchmark_project(name, conn, me, instructions, people)
        except (ValueError, OSError) as exc:
            st.error(str(exc))
            return
        st.cache_resource.clear()
        st.session_state.new_version = v + 1
        st.session_state.manage_project = ws.id
        st.rerun()


def _add_labels(ws: WorkspaceConfig) -> None:
    added = builder.clean_labels(st.text_input("New label buttons, comma separated",
                                               key=f"more_labels_{ws.id}"))
    if st.button("Add labels", key=f"more_labels_save_{ws.id}") and added:
        builder.add_labels(ws, added)
        st.cache_resource.clear()
        st.rerun()


def _add_csv(ws: WorkspaceConfig) -> None:
    # A new uploader after each success, so a second click cannot add the file twice.
    version_key = f"more_version_{ws.id}"
    key = f"more_{ws.id}_{st.session_state.get(version_key, 0)}"
    st.markdown("**Upload a CSV**")
    table = _upload(f"{key}_csv")
    if table is None:
        return
    columns = list(table.columns)
    id_choice = st.selectbox("Column with item ids", [AUTO_ID] + columns, key=f"{key}_id",
                             index=columns.index("id") + 1 if "id" in columns else 0)
    id_column = None if id_choice == AUTO_ID else id_choice
    display = None
    if not ws.projects[0].display:
        rest = [c for c in columns if c != id_column]
        display = st.multiselect("Columns annotators see", rest, default=rest, key=f"{key}_display")
    plan = _split_controls(table, key)
    if st.button("Add batches", type="primary", key=f"{key}_add"):
        if display is not None and not display:
            st.error("Choose at least one column to show.")
            return
        try:
            names = builder.add_data(ws, table, id_column, display=display, **plan)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.cache_resource.clear()
        st.session_state[version_key] = st.session_state.get(version_key, 0) + 1
        st.session_state.builder_note = f"Added {len(names)} batch(es) to {ws.name}."
        st.rerun()


def _assign(ws: WorkspaceConfig) -> None:
    st.markdown("**Batches and assignments**")
    available = list_batches(ws)
    if not available:
        st.caption("No batches yet. Upload a CSV above.")
        return
    data = accounts.load_assignments(ws)
    assigned = {b: [n for n, r in data.items() if b in r.get("batches", [])] for b in available}
    rows = [{"Batch": b,
             "Assigned to": ", ".join(f"{n} {batch_status(ws, n, b)['pct']}%" for n in assigned[b]) or "—"}
            for b in available]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    open_batches = [b for b in available if not assigned[b]]
    chosen = st.multiselect("Batches to assign", available, default=open_batches,
                            key=f"give_b_{ws.id}", help="Starts with the batches nobody has yet.")
    c1, c2 = st.columns([3, 1])
    people = c1.multiselect("Annotators", _people(), key=f"give_p_{ws.id}")
    per = c2.number_input("Annotators per batch", 1, 20, 1, key=f"give_n_{ws.id}")
    if st.button("Assign", type="primary", key=f"give_save_{ws.id}", disabled=not (chosen and people)):
        builder.assign(ws, chosen, people, int(per))
        st.session_state.builder_note = f"Assigned {len(chosen)} batch(es)."
        st.rerun()


def _manage(ws: WorkspaceConfig) -> None:
    if benchmark.is_benchmark(ws):
        from . import form_builder

        form_builder.render(ws)
        return
    if ws.projects[0].task == "choice":
        _add_labels(ws)
    _add_csv(ws)
    st.divider()
    _assign(ws)


def render(workspaces: list[WorkspaceConfig], me: str) -> None:
    note = st.session_state.pop("builder_note", None)
    if note:
        st.success(note)
    projects = {w.id: w for w in workspaces if builder.is_app_project(w)}
    with st.expander("New project", expanded=not projects):
        _create(me)
    if not projects:
        return
    ids = list(projects)
    if st.session_state.get("manage_project") not in ids:
        st.session_state.manage_project = ids[0]
    picked = st.selectbox("Project", ids, key="manage_project", format_func=lambda i: projects[i].name)
    _manage(projects[picked])
