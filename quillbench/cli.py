"""
``quillbench`` -- command-line tool for QuillBench.

    quillbench serve                         run the web app (port and path from settings)
    quillbench validate [WORKSPACE]          check every registered workspace (or one)
    quillbench workspace list|add|remove     the registry of paper workspaces
    quillbench init-workspace FOLDER         start a new paper workspace from a template
    quillbench user list|add|passwd|role|disable|enable
    quillbench assign USER WORKSPACE BATCH...  [--remove]
    quillbench status WORKSPACE              progress per annotator and batch
    quillbench quality WORKSPACE [PROJECT]   agreement, check items, time per item
    quillbench export WORKSPACE [--project P] [--real-ids] [--no-notes]
    quillbench backup                        archive answers + studio state now
    quillbench version
    quillbench connect ADDRESS               (annotators) remember a server and open it
    quillbench open                          (annotators) open the remembered server
"""

from __future__ import annotations

import argparse
import getpass
import os
import subprocess
import sys
from pathlib import Path

from . import NAME, __version__, accounts, client, paths, registry
from .config import MANIFEST_NAME, ConfigError, load_workspace
from .settings import load_settings


def _ws(ident: str):
    workspaces, problems = registry.load_all()
    for ws in workspaces:
        if ident in (ws.id, str(ws.manifest), str(ws.root)):
            return ws
    p = Path(ident).expanduser()
    if p.exists():
        return load_workspace(p)
    sys.exit(f"No workspace '{ident}'. Registered: {[w.id for w in workspaces]}"
             + (f"\nNot loaded: {problems}" if problems else ""))


def _password(prompt="Password: ") -> str:
    pw = os.environ.get("QUILLBENCH_PASSWORD") or getpass.getpass(prompt)
    if not os.environ.get("QUILLBENCH_PASSWORD") and pw != getpass.getpass("Again: "):
        sys.exit("Passwords differ.")
    return pw


def cmd_serve(a):
    s = load_settings()
    app = Path(__file__).parent / "ui" / "app.py"
    args = [sys.executable, "-m", "streamlit", "run", str(app),
            "--server.address", a.address or "127.0.0.1",
            "--server.port", str(a.port or s.port),
            "--server.headless", "true",
            "--server.baseUrlPath", s.base_url_path,
            "--browser.gatherUsageStats", "false",
            "--theme.base", "light", "--theme.primaryColor", "#2B3A67"]
    if s.ssl_cert or s.ssl_key:
        cert, key = Path(s.ssl_cert).expanduser(), Path(s.ssl_key).expanduser()
        missing = [str(p) for p in (cert, key) if not p.is_file()]
        if missing:
            sys.exit(f"HTTPS is set up in settings.yaml, but these files are missing: {missing}")
        args += ["--server.sslCertFile", str(cert), "--server.sslKeyFile", str(key)]
    os.execv(sys.executable, args)


def cmd_validate(a):
    from .validate import validate_workspace

    users = accounts.load_users()
    targets = [_ws(a.workspace)] if a.workspace else registry.load_all()[0]
    _, problems = registry.load_all()
    bad = 0
    for p in problems if not a.workspace else []:
        print(f"✗ manifest: {p}")
        bad += 1
    for ws in targets:
        errors, warnings = validate_workspace(ws, users)
        mark = "✗" if errors else ("!" if warnings else "✓")
        print(f"{mark} {ws.id:18s} {len(ws.projects):2d} projects  {ws.manifest}")
        for e in errors:
            print(f"    error:   {e}")
        for w in warnings if a.verbose or len(warnings) <= 3 else warnings[:3]:
            print(f"    warning: {w}")
        if not a.verbose and len(warnings) > 3:
            print(f"    … {len(warnings) - 3} more warnings (use -v)")
        bad += bool(errors)
    sys.exit(1 if bad else 0)


def cmd_workspace(a):
    if a.action == "list":
        workspaces, problems = registry.load_all()
        for ws in workspaces:
            n = sum(len(v.get("batches", [])) for v in accounts.load_assignments(ws).values())
            print(f"{ws.id:18s} {ws.name:42s} {len(ws.projects):2d} projects  {n:3d} assignments"
                  f"{'  (archived)' if ws.archived else ''}\n{'':18s} {ws.manifest}")
        for p in problems:
            print(f"NOT LOADED: {p}")
    elif a.action == "add":
        ws = registry.add(a.path)
        print(f"Registered '{ws.id}' ({ws.manifest}).")
    elif a.action == "remove":
        print("Unregistered." if registry.remove(a.path) else "Not registered.")


TEMPLATE = """\
# QuillBench workspace for one paper.
# Every relative path is resolved from this file's folder.
workspace:
  id: {id}
  name: {name}
  owner: A.C. Pogaku
  description: ""
  batches_dir: batches          # one file per batch: <name>.csv or <name>.jsonl, with an 'id' column
  progress_dir: progress        # answers: <annotator>__<batch>.csv (written by the studio)
  assignments_file: quillbench_assignments.json

projects:
  - id: {id}_rating
    name: Example rating task
    task: rating                # rating | choice | text | verdict | ... (see README)
    batch_prefix: rate_         # batches named rate_*.csv belong to this project
    scale: {{min: 0, max: 1, step: 0.05, left_label: "not at all", right_label: "completely",
            unsure_label: "Can't judge"}}
    display:                    # which item columns to show, in order
      - {{field: question, label: Question}}
      - {{field: context, as: markdown, collapsed: true}}
    instructions: |
      Write what the annotator must decide here. It is shown above every item.
    checks: {{}}                 # item id -> expected answer, for hidden attention checks
"""


def cmd_init(a):
    folder = Path(a.folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / MANIFEST_NAME
    if target.exists():
        sys.exit(f"{target} already exists.")
    ws_id = a.id or folder.name.lower().replace(" ", "_").replace("-", "_")
    target.write_text(TEMPLATE.format(id=ws_id, name=a.name or folder.name), encoding="utf-8")
    (folder / "batches").mkdir(exist_ok=True)
    (folder / "progress").mkdir(exist_ok=True)
    print(f"Created {target}\nEdit it, put batch files in {folder / 'batches'}, then run:\n"
          f"  quillbench workspace add {folder}")


def cmd_user(a):
    if a.action == "list":
        for name, r in sorted(accounts.load_users().items()):
            print(f"{name:24s} {r.get('role', ''):10s} {'active' if r.get('active', True) else 'disabled':9s}"
                  f" {r.get('display_name', '')}")
    elif a.action == "add" and a.ldap:
        if a.admin:
            sys.exit(accounts.NO_LDAP_ADMIN)
        accounts.add_ldap_user(a.name, "annotator", a.display or "")
        print(f"Added {a.name} (university login).")
    elif a.action == "add":
        accounts.add_user(a.name, _password(), "admin" if a.admin else "annotator", a.display or "")
        print(f"Added {a.name}.")
    elif a.action == "passwd":
        accounts.set_password(a.name, _password("New password: "))
        print("Password changed.")
    elif a.action == "role":
        try:
            accounts.update_user(a.name, role=a.value)
        except ValueError as exc:
            sys.exit(str(exc))
        print("Role changed.")
    elif a.action in ("disable", "enable"):
        accounts.update_user(a.name, active=a.action == "enable")
        print(f"{a.name} {a.action}d.")


def cmd_assign(a):
    ws = _ws(a.workspace)
    if a.user not in accounts.load_users():
        sys.exit(f"No account '{a.user}'. Add it with: quillbench user add {a.user}")
    from .storage import list_batches

    known = set(list_batches(ws))
    missing = [b for b in a.batches if b not in known]
    if missing and not a.remove:
        sys.exit(f"No batch file for {missing} in {ws.id}.")
    data = accounts.load_assignments(ws)
    cur = data.get(a.user, {}).get("batches", [])
    cur = [b for b in cur if b not in a.batches] if a.remove else cur + [b for b in a.batches if b not in cur]
    if cur:
        data[a.user] = {"batches": cur}
    else:
        data.pop(a.user, None)
    accounts.save_assignments(ws, data)
    print(f"{a.user} in {ws.id}: {cur}")


def cmd_status(a):
    import pandas as pd

    from .storage import progress_summary_rows

    rows, problems = progress_summary_rows(_ws(a.workspace))
    if rows:
        df = pd.DataFrame(rows).drop(columns=["_file", "_project", "Answers"])
        print(df.to_string(index=False))
    for p in problems:
        print("warning:", p)


def cmd_quality(a):
    import pandas as pd

    from .quality import report

    ws = _ws(a.workspace)
    for p in ws.projects:
        if a.project and p.id != a.project:
            continue
        rep = report(ws, p)
        if not rep["answers"]:
            continue
        alpha = "n/a" if rep["alpha"] is None else f"{rep['alpha']:.3f}"
        print(f"\n== {p.name} ({p.id}) — alpha[{rep['level']}] = {alpha}, "
              f"{rep['items_with_2plus']} items with 2+ answers, {rep['answers']} answers")
        if rep["people"]:
            print(pd.DataFrame(rep["people"]).to_string(index=False))
        if rep["pairs"]:
            print(pd.DataFrame(rep["pairs"]).to_string(index=False))


def cmd_export(a):
    from .export import export

    out = export(_ws(a.workspace), a.project or None, anonymise=not a.real_ids,
                 include_notes=not a.no_notes)
    print(f"Exported to {out}")


def cmd_backup(a):
    from .backup import run

    print(f"Backup written: {run(a.dest)}")


def cmd_connect(a):
    url = client.find_server(a.address)
    client.save(url)
    print(f"Connected to {url}")
    if url.startswith("http://"):
        print("Warning: this connection is not encrypted, so your password can be read on the "
              "network. Ask the server's admin for an https:// address.")
    print("Next time, just run: quillbench open")
    if not a.no_open:
        client.open_server(url)


def cmd_open(a):
    url = client.saved()
    if not url:
        sys.exit("No server yet. Run: quillbench connect <address you were given>")
    client.open_server(url)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="quillbench", description=f"{NAME} {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve"); s.add_argument("--port", type=int); s.add_argument("--address")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("validate"); s.add_argument("workspace", nargs="?")
    s.add_argument("-v", "--verbose", action="store_true"); s.set_defaults(fn=cmd_validate)
    s = sub.add_parser("workspace"); s.add_argument("action", choices=["list", "add", "remove"])
    s.add_argument("path", nargs="?"); s.set_defaults(fn=cmd_workspace)
    s = sub.add_parser("init-workspace"); s.add_argument("folder"); s.add_argument("--id")
    s.add_argument("--name"); s.set_defaults(fn=cmd_init)
    s = sub.add_parser("user")
    s.add_argument("action", choices=["list", "add", "passwd", "role", "disable", "enable"])
    s.add_argument("name", nargs="?"); s.add_argument("value", nargs="?")
    s.add_argument("--admin", action="store_true"); s.add_argument("--display")
    s.add_argument("--ldap", action="store_true", help="signs in with their university (LDAP) password")
    s.set_defaults(fn=cmd_user)
    s = sub.add_parser("assign"); s.add_argument("user"); s.add_argument("workspace")
    s.add_argument("batches", nargs="+"); s.add_argument("--remove", action="store_true")
    s.set_defaults(fn=cmd_assign)
    s = sub.add_parser("status"); s.add_argument("workspace"); s.set_defaults(fn=cmd_status)
    s = sub.add_parser("quality"); s.add_argument("workspace"); s.add_argument("project", nargs="?")
    s.set_defaults(fn=cmd_quality)
    s = sub.add_parser("export"); s.add_argument("workspace")
    s.add_argument("--project", action="append"); s.add_argument("--real-ids", action="store_true")
    s.add_argument("--no-notes", action="store_true"); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("backup"); s.add_argument("--dest"); s.set_defaults(fn=cmd_backup)
    s = sub.add_parser("connect", help="annotators: remember a server and open it")
    s.add_argument("address"); s.add_argument("--no-open", action="store_true")
    s.set_defaults(fn=cmd_connect)
    s = sub.add_parser("open", help="annotators: open the remembered server")
    s.set_defaults(fn=cmd_open)
    s = sub.add_parser("version")
    s.set_defaults(fn=lambda a: print(f"{NAME} {__version__} · studio home {paths.studio_home()}"))

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except (ConfigError, ValueError) as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
