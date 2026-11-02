"""Admin: people, assignments, workspaces and archiving, export and backup."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import accounts, backup, export
from ..config import WorkspaceConfig
from ..settings import StudioSettings, save_archived_projects, save_setting
from ..storage import batch_status, list_batches
from . import builder as project_builder


def render(ws: WorkspaceConfig, workspaces: list[WorkspaceConfig], me: str,
           settings: StudioSettings) -> None:
    t_proj, t_assign, t_people, t_ws, t_export = st.tabs(
        ["Projects", "Assignments", "People", "Workspaces & archive", "Export & backup"])
    with t_proj:
        project_builder.render(ws, me)
    with t_assign:
        _assignments(ws)
    with t_people:
        _people(me)
    with t_ws:
        _workspaces(workspaces, settings)
    with t_export:
        _export(ws)


def _assignments(ws: WorkspaceConfig) -> None:
    users = accounts.load_users()
    data = accounts.load_assignments(ws)
    available = list_batches(ws)
    st.caption(f"Saved in this paper's folder: `{ws.assignments_file}`")

    people = sorted(u for u, r in users.items() if r.get("active", True))
    who = st.selectbox("Person", people, key=f"asg_who_{ws.id}")
    if who:
        current = [str(b) for b in data.get(who, {}).get("batches", [])]
        unknown = [b for b in current if b not in available]
        if unknown:
            st.caption(f"Kept but not selectable (no batch file found): {', '.join(unknown)}")
        chosen = st.multiselect("Batches", available, default=[b for b in current if b in available],
                                key=f"asg_b_{ws.id}_{who}")
        if st.button("Save assignment", type="primary", key=f"asg_save_{ws.id}_{who}"):
            # Batches whose file is temporarily absent are preserved, so a save can
            # never silently drop someone's work queue because a file was moved.
            new = chosen + unknown
            if new:
                data[who] = {"batches": new}
            else:
                data.pop(who, None)
            accounts.save_assignments(ws, data)
            st.success("Saved.")
            st.rerun()

    rows = []
    for b in available:
        try:
            proj = ws.project_for_batch(b)
            pname = proj.name + (" (archived)" if proj.archived else "")
        except Exception:
            pname = "— unrouted —"
        names = [n for n, r in data.items() if b in r.get("batches", [])]
        done = [f"{n} {batch_status(ws, n, b)['pct']}%" for n in names]
        rows.append({"Batch": b, "Project": pname, "Assigned to": ", ".join(done) or "—",
                     "# people": len(names)})
    if rows:
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True, height=380)


def _people(me: str) -> None:
    users = accounts.load_users()
    st.caption("Accounts are shared by all workspaces and stored with hashed passwords in the "
               "studio's private folder.")
    rows = [{"User ID": n, "Name": r.get("display_name", n), "Role": r.get("role"),
             "Active": r.get("active", True)} for n, r in sorted(users.items())]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Add a person**")
        with st.form("add_user", clear_on_submit=True):
            uid = st.text_input("User ID")
            name = st.text_input("Display name")
            pw = st.text_input("Password (8+ characters)", type="password")
            role = st.selectbox("Role", accounts.ROLES)
            if st.form_submit_button("Add", width="stretch"):
                try:
                    accounts.add_user(uid, pw, role, name)
                    st.success(f"Added {uid}.")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
    with c2:
        st.markdown("**Change a person**")
        who = st.selectbox("Person", sorted(users), key="edit_who")
        if who:
            rec = users[who]
            with st.form(f"edit_{who}"):
                pw = st.text_input("New password (leave blank to keep)", type="password")
                role = st.selectbox("Role", accounts.ROLES,
                                    index=accounts.ROLES.index(rec.get("role", "annotator")))
                active = st.checkbox("Active (can sign in)", value=rec.get("active", True),
                                     disabled=who == me)
                if st.form_submit_button("Save", width="stretch"):
                    try:
                        if pw:
                            accounts.set_password(who, pw)
                        accounts.update_user(who, role=role, active=active or who == me)
                        st.success("Saved.")
                        st.rerun()
                    except ValueError as exc:
                        st.error(str(exc))


def _workspaces(workspaces: list[WorkspaceConfig], settings: StudioSettings) -> None:
    st.caption("Archiving hides a workspace or project from annotators. Nothing is deleted or "
               "moved; un-archive at any time.")
    archived_ws = set(settings.archived_workspaces or [])
    archived_proj = {k: set(v) for k, v in (settings.archived_projects or {}).items()}
    for w in workspaces:
        with st.expander(f"{w.name} (`{w.id}`) — {len(w.projects)} projects"
                         + (" · archived" if w.archived else "")):
            st.caption(f"Manifest: `{w.manifest}`")
            if w.description:
                st.write(w.description)
            new_ws = st.checkbox("Archive this workspace", value=w.id in archived_ws,
                                 key=f"arch_ws_{w.id}")
            rows = []
            picks = {}
            for p in w.projects:
                picks[p.id] = st.checkbox(f"Archive {p.name} (`{p.id}`, task {p.task})",
                                          value=p.archived, key=f"arch_{w.id}_{p.id}")
                rows.append(p.id)
            if st.button("Save", key=f"arch_save_{w.id}"):
                if new_ws:
                    archived_ws.add(w.id)
                else:
                    archived_ws.discard(w.id)
                archived_proj[w.id] = {pid for pid, on in picks.items() if on}
                save_setting("archived_workspaces", sorted(archived_ws))
                save_archived_projects(archived_proj)
                st.cache_resource.clear()
                st.success("Saved.")
                st.rerun()
    st.caption("Add a new paper from the command line: `labelwise init-workspace <folder>` then "
               "`labelwise workspace add <folder>`.")


def _export(ws: WorkspaceConfig) -> None:
    st.markdown(f"**Export {ws.name}**")
    ids = st.multiselect("Projects (empty = all)", [p.id for p in ws.projects], key=f"exp_{ws.id}")
    anon = st.checkbox("Replace annotator ids with codes (A01, A02, …)", value=True)
    notes = st.checkbox("Include notes", value=True)
    if st.button("Export answers", type="primary"):
        out = export.export(ws, ids or None, anonymise=anon, include_notes=notes)
        st.success(f"Written to `{out}`")
        csv = (out / "answers.csv").read_bytes()
        st.download_button("Download answers.csv", csv, file_name=f"{ws.id}_answers.csv")
        st.download_button("Download DATASHEET.md", (out / "DATASHEET.md").read_bytes(),
                           file_name=f"{ws.id}_DATASHEET.md")
    st.divider()
    st.markdown("**Backup** (runs nightly; this makes one now)")
    if st.button("Back up now"):
        st.success(f"Written to `{backup.run()}`")
