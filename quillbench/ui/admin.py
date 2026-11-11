"""Admin: people, assignments, workspaces and archiving, export and backup."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import accounts, backup, benchmark, connections, export
from ..config import WorkspaceConfig
from .. import ldap_auth
from ..settings import StudioSettings, save_archived_projects, save_setting
from ..storage import batch_status, list_batches
from . import builder as project_builder


def render(ws: WorkspaceConfig, workspaces: list[WorkspaceConfig], me: str,
           settings: StudioSettings) -> None:
    t_proj, t_assign, t_people, t_conn, t_ws, t_export = st.tabs(
        ["Projects", "Assignments", "People", "Connections", "Workspaces & archive", "Export & backup"])
    with t_proj:
        project_builder.render(workspaces, me)
    with t_conn:
        _connections(workspaces)
    with t_assign:
        _assignments(ws)
    with t_people:
        _people(me, ldap_auth.enabled(settings.ldap))
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


def _people(me: str, ldap_on: bool = False) -> None:
    users = accounts.load_users()
    st.caption("Accounts are shared by all workspaces and stored with hashed passwords in the "
               "studio's private folder. Admins are added on the server only: "
               "`quillbench user add ID --admin`. University (LDAP) accounts cannot be admins.")
    rows = [{"User ID": n, "Name": r.get("display_name", n), "Role": r.get("role"),
             "Sign-in": "University" if r.get("source") == "ldap" and not r.get("password_hash") else "Password",
             "Active": r.get("active", True)} for n, r in sorted(users.items())]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Add a person**")
        with st.form("add_user", clear_on_submit=True):
            uid = st.text_input("User ID")
            name = st.text_input("Display name")
            uni = ldap_on and st.checkbox("Signs in with their university login", value=True,
                                          help="Their university password is checked by LDAP; leave "
                                               "the password below empty.")
            pw = st.text_input("Password (8+ characters)", type="password")
            st.caption("Added as an annotator.")
            if st.form_submit_button("Add", width="stretch"):
                try:
                    if uni:
                        accounts.add_ldap_user(uid, "annotator", name)
                    else:
                        accounts.add_user(uid, pw, "annotator", name)
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
                st.caption(f"Role: {rec.get('role', 'annotator')} (changed on the server only)")
                active = st.checkbox("Active (can sign in)", value=rec.get("active", True),
                                     disabled=who == me)
                if st.form_submit_button("Save", width="stretch"):
                    try:
                        if pw:
                            accounts.set_password(who, pw)
                        accounts.update_user(who, active=active or who == me)
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
    st.caption("Add a new paper from the command line: `quillbench init-workspace <folder>` then "
               "`quillbench workspace add <folder>`.")


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


def connections_page(workspaces: list[WorkspaceConfig]) -> None:
    _connections(workspaces)


def people_page(me: str, settings: StudioSettings) -> None:
    _people(me, ldap_auth.enabled(settings.ldap))


def _connections(workspaces: list[WorkspaceConfig]) -> None:
    st.caption("Databases that benchmark projects query. Login details stay in the studio's "
               "private folder on the server; people writing entries see only the name. Every "
               "query runs read-only, but a database user that can only read is still the safest.")
    conns = connections.load_all()
    used: dict[str, list[str]] = {}
    for w in workspaces:
        if benchmark.is_benchmark(w):
            used.setdefault(benchmark.load_form(w).connection, []).append(w.name)
    if conns:
        st.dataframe(pd.DataFrame([{"Id": c.id, "Name": c.name, "Engine": connections.ENGINES[c.engine],
                                    "Where": c.where, "Used by": ", ".join(used.get(c.id, [])) or "—"}
                                   for c in conns.values()]), width="stretch", hide_index=True)
    ids = ["(new connection)"] + list(conns)
    pick = st.selectbox("Add or change", ids, key="conn_pick")
    cur = conns.get(pick)
    engines = list(connections.ENGINES)
    ekey, note_key = f"conn_engine_{pick}", f"conn_found_{pick}"
    if ekey not in st.session_state:
        st.session_state[ekey] = cur.engine if cur else None

    st.markdown("**Where is the database?**")
    c1, c2, c3 = st.columns([3, 1, 1.2], vertical_alignment="bottom")
    host = c1.text_input("Host (name or IP of the database server)", key=f"conn_host_{pick}",
                         value=cur.host if cur and cur.engine != "sqlite" else "",
                         placeholder="db.example.org, or db.example.org:5432")
    port = c2.number_input("Port", 0, 65535, key=f"conn_port_{pick}",
                           value=int(cur.port) if cur and cur.port else 0,
                           help="0 = take it from the host field, or the type's usual port.")
    if c3.button("Detect type", key=f"conn_detect_{pick}", width="stretch", disabled=not host.strip(),
                 help="Asks the port what is listening there. No login is needed for this."):
        h, p = connections.split_address(host, int(port))
        if not p:
            st.session_state[note_key] = (None, "Enter the port the database listens on.")
        else:
            with st.spinner(f"Checking {h}:{p}…"):
                found = connections.detect(h, p)
            st.session_state[note_key] = (found.engine, found.message)
            if found.engine:
                st.session_state[ekey] = found.engine
    found = st.session_state.get(note_key)
    if found and found[0]:
        st.success(f"Found {found[1]}.")
    elif found:
        st.warning(found[1])

    engine = st.selectbox("Type", engines, format_func=connections.ENGINES.get, key=ekey,
                          placeholder="Detect it above, or choose")
    if engine is None:
        st.caption("Click **Detect type**, or choose the type yourself. A SQLite file on this "
                   "server needs no host: choose SQLite.")
        return
    with st.form(f"conn_form_{pick}"):
        c1, c2 = st.columns(2)
        cid = c1.text_input("Id (short, no spaces)", value=cur.id if cur else "", disabled=bool(cur))
        name = c2.text_input("Name people see", value=cur.name if cur else "")
        database = user = password = path = ""
        if engine == "sqlite":
            path = st.text_input("Path of the database file on the server", value=cur.path if cur else "")
        else:
            c5, c6, c7 = st.columns(3)
            database = c5.text_input("Database" + (" (optional)" if engine == "neo4j" else ""),
                                     value=cur.database if cur else "")
            user = c6.text_input("User", value=cur.user if cur else "")
            password = c7.text_input("Password" + (" (blank keeps the saved one)" if cur else ""),
                                     type="password")
        b1, b2 = st.columns(2)
        save = b1.form_submit_button("Save and test", type="primary", width="stretch")
        remove = b2.form_submit_button("Delete", width="stretch", disabled=not cur)
    if save:
        h, p = ("", 0) if engine == "sqlite" else connections.split_address(host, int(port))
        if engine != "sqlite" and not p:
            p = connections.DEFAULT_PORTS[engine]
        conn = connections.Connection(id=(cur.id if cur else cid.strip()), name=name.strip(), engine=engine,
                                      host=h, port=p, database=database.strip(),
                                      user=user.strip(), password=password, path=path.strip())
        try:
            connections.save(conn)
        except ValueError as exc:
            st.error(str(exc))
            return
        error = connections.test(connections.get(conn.id))
        if error:
            st.warning(f"Saved, but a test query failed: {error}")
        else:
            st.success("Saved. A test query worked.")
    if remove and cur:
        if used.get(cur.id):
            st.error(f"Still used by: {', '.join(used[cur.id])}. Choose another connection there first.")
        else:
            connections.delete(cur.id)
            st.success("Deleted.")
            st.rerun()
