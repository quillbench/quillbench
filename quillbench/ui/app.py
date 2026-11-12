"""
QuillBench -- the web app.

    quillbench serve            (or: streamlit run quillbench/ui/app.py)

The studio serves every workspace listed in its registry. Each workspace's
manifest is read on load; answers are written straight into that paper's own
folders. A manifest that fails to load is reported to admins and skipped, so one
broken paper never takes the others offline.
"""

from __future__ import annotations

import html
import sys
import time
from pathlib import Path

# Streamlit runs this file as a script; make the package importable from a checkout.
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

from quillbench import AUTHOR, SHORT_NAME, __version__, accounts, benchmark, paths, registry  # noqa: E402
from quillbench.settings import load_settings  # noqa: E402
from quillbench.storage import batch_status  # noqa: E402
from quillbench.ui import admin, console, creator, dashboard, generic, query_review, shortcuts  # noqa: E402
from quillbench.ui.session import emit_auth_cookie, expire_auth_cookie, restore_session  # noqa: E402
from quillbench.ui.theme import inject_css, login_brand, login_head, top_bar  # noqa: E402
from quillbench.validate import validate_workspace  # noqa: E402


def _stamp() -> tuple:
    """Changes whenever the registry, settings or any manifest changes."""
    files = [paths.registry_file(), paths.settings_file(), *registry.manifest_paths()]
    return tuple(f.stat().st_mtime if f.exists() else 0.0 for f in files)


@st.cache_resource(show_spinner=False)
def _load(stamp: tuple):
    settings = load_settings()
    workspaces, problems = registry.load_all(settings)
    checks = {ws.id: validate_workspace(ws) for ws in workspaces}
    return settings, workspaces, problems, checks


from quillbench.ui import builder as project_builder  # noqa: E402

settings, workspaces, load_problems, checks = _load(_stamp())

st.set_page_config(page_title=settings.title, page_icon=str(Path(__file__).parent / "assets" / "icon.png"), layout="wide",
                   initial_sidebar_state="auto")
inject_css()

for key, default in (("logged_in", False), ("annotator", ""), ("active_batch", None),
                     ("auth_issued", 0), ("auth_epoch", 0),
                     ("query_idx", 0), ("just_signed_out", False), ("workspace", None)):
    st.session_state.setdefault(key, default)

users = accounts.load_users()
by_id = {ws.id: ws for ws in workspaces}


def _sign_out() -> None:
    for k, v in (("logged_in", False), ("annotator", ""), ("active_batch", None),
                 ("query_idx", 0), ("workspace", None), ("just_signed_out", True)):
        st.session_state[k] = v


# A live session ends as soon as the account is disabled or deleted, its password
# changes, or it signs out elsewhere (each bumps session_epoch).
if st.session_state.logged_in:
    me_rec = users.get(st.session_state.annotator)
    if (not me_rec or not me_rec.get("active", True)
            or accounts.session_epoch(st.session_state.annotator, users) != st.session_state.auth_epoch
            or time.time() - st.session_state.auth_issued > settings.session_max_age_days * 86400):
        _sign_out()

# Restore sign-in after a reconnect wiped session_state, and the working position with it.
if not st.session_state.logged_in and not st.session_state.just_signed_out:
    restored = restore_session(settings, users)
    if restored:
        st.session_state.logged_in = True
        st.session_state.annotator, st.session_state.auth_issued = restored
        st.session_state.auth_epoch = accounts.session_epoch(restored[0], users)
        qp = st.query_params
        if qp.get("w") in by_id:
            st.session_state.workspace = qp.get("w")
        if qp.get("b"):
            st.session_state.active_batch = qp.get("b")
        try:
            st.session_state.query_idx = max(0, int(qp.get("i", "0")))
        except ValueError:
            pass

# Query-param writes issued right before st.rerun() are dropped, so the URL is
# synced here, on the pass after any login or navigation rerun.
if st.session_state.logged_in:
    emit_auth_cookie(settings, st.session_state.annotator, st.session_state.auth_issued, users)
    desired = {"i": str(st.session_state.query_idx)}
    if st.session_state.workspace:
        desired["w"] = str(st.session_state.workspace)
    if st.session_state.active_batch:
        desired["b"] = str(st.session_state.active_batch)
    if any(st.query_params.get(k) != v for k, v in desired.items()):
        st.query_params.from_dict(desired)
else:
    if any(st.query_params.get(k) for k in ("w", "b", "i")):
        st.query_params.clear()
    if st.session_state.just_signed_out:
        expire_auth_cookie(settings)
    st.session_state.just_signed_out = False

me = st.session_state.annotator
user_is_admin = st.session_state.logged_in and accounts.is_admin(me, users)
if not user_is_admin:
    # Tables get a hover toolbar with "Download as CSV"; only admins may download data.
    st.markdown("<style>[data-testid='stElementToolbar']{display:none !important}</style>",
                unsafe_allow_html=True)


def visible_batches(ws, name: str) -> list[str]:
    """This person's batches in a workspace, without archived projects' batches."""
    out = []
    for b in accounts.assigned_batches(ws, name):
        try:
            if ws.project_for_batch(b).archived:
                continue
        except Exception:
            pass
        out.append(b)
    return out


# ── Sign-in: a card on the main page (on a phone the sidebar starts hidden) ──
if not st.session_state.logged_in:
    with st.container(key="quillbench_login"):
        brand, form = st.columns([1.05, 1])
        with brand:
            login_brand(settings.subtitle)
        with form:
            if not users:
                login_head("Set up QuillBench", "No accounts yet.")
                st.markdown("The first admin is made on the server, by someone who can run "
                            "QuillBench there:")
                st.code("quillbench user add YOUR_ID --admin", language="bash")
                st.caption("Then reload this page and sign in.")
            if users:
                login_head("Welcome back", f"Sign in to {settings.title} to continue your work.")
                with st.form("login", border=False):
                    name_in = st.text_input("User ID", placeholder="Your assigned identifier")
                    pass_in = st.text_input("Password", type="password", placeholder="••••••••")
                    if st.form_submit_button("Sign in", width="stretch", type="primary"):
                        name_in = name_in.strip()
                        if accounts.login_locked(name_in):
                            st.error("Too many wrong attempts. Try again in 15 minutes.")
                        elif accounts.sign_in(name_in, pass_in, users, settings.ldap):
                            accounts.record_login(name_in, True)
                            st.session_state.logged_in = True
                            st.session_state.annotator = name_in
                            st.session_state.auth_issued = int(time.time())
                            st.session_state.auth_epoch = accounts.session_epoch(name_in, users)
                            st.rerun()
                        else:
                            accounts.record_login(name_in, False)
                            st.error("Wrong user ID or password.")
                help_text = ("Use your university login, or the one your study lead gave you."
                             if settings.ldap else "No login yet? Ask your study lead for one.")
                st.markdown(f'<div class="quillbench-login-help">{help_text}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="quillbench-login-foot">{SHORT_NAME} {__version__} · © {AUTHOR}</div>',
                        unsafe_allow_html=True)
    st.stop()

# ── Sidebar ───────────────────────────────────────────────────────────────
display = users.get(me, {}).get("display_name") or me
with st.sidebar:
    st.markdown(f'<p class="sidebar-user">{html.escape(display)}</p>'
                f'<p class="sidebar-role">{html.escape(users.get(me, {}).get("role", "annotator").capitalize())}'
                f' · <code>{html.escape(me)}</code></p>', unsafe_allow_html=True)
    if user_is_admin:
        my_workspaces = [ws for ws in workspaces if not ws.archived] + \
                        [ws for ws in workspaces if ws.archived]
    else:
        my_workspaces = [ws for ws in workspaces if not ws.archived
                         and (visible_batches(ws, me) or benchmark.is_participant(ws, me))]
    if my_workspaces:
        ids = [ws.id for ws in my_workspaces]
        if st.session_state.workspace not in ids:
            st.session_state.workspace = ids[0]
        picked = st.selectbox(
            "Workspace (paper)", ids, index=ids.index(st.session_state.workspace),
            format_func=lambda i: by_id[i].name + (" · archived" if by_id[i].archived else ""))
        if picked != st.session_state.workspace:
            st.session_state.workspace = picked
            st.session_state.active_batch = None
            st.session_state.query_idx = 0
            st.rerun()
    st.toggle("Dark interface", key="dark_mode", help="Low-glare palette for long sessions.")
    if st.button("Sign out", width="stretch", help="Signs you out on every device."):
        accounts.revoke_sessions(me)
        _sign_out()
        st.rerun()
    st.divider()
    st.markdown(
        f'<p class="sidebar-footnote">{settings.footnote + "<br>" if settings.footnote else ""}'
        f"{SHORT_NAME} {__version__}<br>© {AUTHOR}</p>", unsafe_allow_html=True)

top_bar(settings.title, settings.subtitle,
        by_id[st.session_state.workspace].name if my_workspaces else "", display)

if load_problems and user_is_admin:
    with st.expander(f"⚠ {len(load_problems)} workspace(s) could not be loaded", expanded=True):
        for p in load_problems:
            st.error(p)

if not my_workspaces and user_is_admin:
    st.info("Create your first project. A benchmark project needs its database first: add it "
            "under Connections.")
    t_new, t_conn, t_people = st.tabs(["New project", "Connections", "People"])
    with t_new:
        project_builder.render([], me)
    with t_conn:
        admin.connections_page([])
    with t_people:
        admin.people_page(me, settings)
    st.stop()

if not my_workspaces:
    st.info("Nothing is assigned to you right now. Please contact the study owner.")
    st.stop()

ws = by_id[st.session_state.workspace]
errors, warnings = checks.get(ws.id, ([], []))
# Always present, so the tabs below keep their place when the check comes or goes.
check_slot = st.container()
if user_is_admin and (errors or warnings):
    with check_slot.expander(f"Workspace check: {len(errors)} error(s), {len(warnings)} warning(s)",
                             expanded=bool(errors)):
        for e in errors:
            st.error(e)
        for w in warnings:
            st.warning(w)

is_bench = benchmark.is_benchmark(ws)
work_tab = "Create" if is_bench else "Annotate"
tab_names = [work_tab] + (["Schema"] if is_bench else []) + ["Progress"] + (["Profile"] if is_bench else [])
# Admins only: the console can load any item's reference query, which would let a
# blind reviewer see what the study hides from them.
if ws.graph_targets and user_is_admin:
    tab_names.append("Query console")
if user_is_admin:
    tab_names.append("Admin")
tabs = dict(zip(tab_names, st.tabs(tab_names)))

with tabs[work_tab]:
    my_batches = [] if is_bench else visible_batches(ws, me)
    if is_bench:
        creator.render(ws, me, is_admin=user_is_admin)
    elif not my_batches:
        st.info("No batches are assigned to you in this workspace.")
    else:
        status = {b: batch_status(ws, me, b) for b in my_batches}
        options = []
        for b in my_batches:
            s = status[b]
            icon = "●" if s["pct"] == 100 else ("◐" if s["pct"] > 0 else "○")
            options.append(f"{icon}  {b}   ·  {s['labeled']}/{s['total']}")
        unfinished = [b for b in my_batches if status[b]["pct"] < 100]
        if st.session_state.active_batch not in my_batches:
            # Open where the work is: the first batch not yet finished.
            st.session_state.active_batch = (unfinished or my_batches)[0]
            st.session_state.query_idx = 0
        current = my_batches.index(st.session_state.active_batch)
        bar = st.container(key="quillbench_workbar").columns([4, 3, 1.4, 1.2], vertical_alignment="bottom")
        with bar[0]:
            picked = st.selectbox("Batch", options, index=current,
                                  help="● complete · ◐ in progress · ○ not started")
        active = my_batches[options.index(picked)]
        later = [b for b in unfinished if b != active]
        next_batch = later[0] if later else None
        project = ws.project_for_batch(active)
        shortcuts.install()
        if project.task_spec.view == "generic":
            generic.render(ws, me, active, bar, next_batch)
        else:
            query_review.render(ws, me, accounts.legacy_view(ws, users), active, bar, next_batch)

if "Schema" in tabs:
    with tabs["Schema"]:
        creator.schema_page(ws)

if "Profile" in tabs:
    with tabs["Profile"]:
        creator.profile(ws, me, user_is_admin)

with tabs["Progress"]:
    if is_bench:
        creator.progress(ws, me, user_is_admin)
    else:
        dashboard.render(ws, me, user_is_admin)

if "Query console" in tabs:
    with tabs["Query console"]:
        console.render(ws)

if "Admin" in tabs:
    with tabs["Admin"]:
        admin.render(ws, workspaces, me, settings)
