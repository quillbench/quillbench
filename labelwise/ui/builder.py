"""Admin screens to create a project and add to one, with no files to edit."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import accounts, builder
from ..config import ConfigError, WorkspaceConfig

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
    st.markdown("**Split and assign**")
    c1, c2 = st.columns(2)
    size = c1.number_input("Items per batch (0 = one batch)", 0, len(table), 0, key=f"{key}_size")
    per = c2.number_input("Annotators per batch", 1, 20, 1, key=f"{key}_per",
                          help="Above 1, each batch goes to several people, for agreement scores.")
    shuffle = st.checkbox("Shuffle items first", key=f"{key}_shuffle")
    people = st.multiselect("Annotators", _people(), key=f"{key}_people",
                            help="Batches are dealt to them in turn. Add people in the People tab.")
    batches = len(builder.split(table, int(size)))
    st.caption(f"This makes {batches} batch(es).")
    return {"batch_size": int(size), "shuffle": shuffle, "people": people, "per_batch": int(per)}


def _create(me: str) -> None:
    st.markdown("**New project**")
    name = st.text_input("Project name", key="new_name")
    task = st.selectbox("What annotators do", list(builder.TASKS), key="new_task",
                        format_func=builder.TASKS.get)
    labels, scale = None, None
    if task == "choice":
        labels = builder.clean_labels(st.text_area("Label buttons (one per line)", key="new_labels"))
    elif task == "rating":
        c1, c2, c3 = st.columns(3)
        scale = {"min": c1.number_input("Lowest", value=0.0, key="new_min"),
                 "max": c2.number_input("Highest", value=1.0, key="new_max"),
                 "step": c3.number_input("Step", value=0.1, min_value=0.001, key="new_step")}
    instructions = st.text_area("Instructions for annotators", key="new_instructions")
    table = _upload("new_csv")
    if table is None:
        return
    columns = list(table.columns)
    id_choice = st.selectbox("Column with item ids", [AUTO_ID] + columns, key="new_id",
                             index=columns.index("id") + 1 if "id" in columns else 0)
    id_column = None if id_choice == AUTO_ID else id_choice
    display = st.multiselect("Columns annotators see", [c for c in columns if c != id_column],
                             default=[c for c in columns if c != id_column], key="new_display")
    plan = _split_controls(table, "new")
    if st.button("Create project", type="primary", key="new_create"):
        try:
            ws = builder.create_project(name, task, instructions, builder.with_ids(table, id_column),
                                        display, me, labels=labels, scale=scale, **plan)
        except (ValueError, ConfigError, OSError) as exc:
            st.error(str(exc))
            return
        st.cache_resource.clear()
        st.session_state.workspace = ws.id
        st.rerun()


def _manage(ws: WorkspaceConfig) -> None:
    project = ws.projects[0]
    st.markdown(f"**Add to {ws.name}**")
    if project.task == "choice":
        added = builder.clean_labels(st.text_input("New label buttons, comma separated",
                                                   key=f"more_labels_{ws.id}"))
        if st.button("Add labels", key=f"more_labels_save_{ws.id}") and added:
            builder.add_labels(ws, added)
            st.cache_resource.clear()
            st.rerun()
    table = _upload(f"more_csv_{ws.id}")
    if table is None:
        return
    columns = list(table.columns)
    id_choice = st.selectbox("Column with item ids", [AUTO_ID] + columns, key=f"more_id_{ws.id}",
                             index=columns.index("id") + 1 if "id" in columns else 0)
    plan = _split_controls(table, f"more_{ws.id}")
    if st.button("Add batches", type="primary", key=f"more_add_{ws.id}"):
        try:
            names = builder.add_data(ws, table, None if id_choice == AUTO_ID else id_choice, **plan)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.cache_resource.clear()
        st.success(f"Added {len(names)} batch(es).")


def render(ws: WorkspaceConfig | None, me: str) -> None:
    if ws is not None and builder.is_app_project(ws):
        _manage(ws)
        st.divider()
    _create(me)
