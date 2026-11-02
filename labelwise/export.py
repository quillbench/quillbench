"""
One-command export of a workspace's answers for release or analysis.

Writes ``<workspace>/labelwise_exports/<timestamp>/``:

* ``answers.csv`` and ``answers.jsonl`` -- one row per (annotator, item);
* ``agreement.csv`` -- alpha per project;
* ``DATASHEET.md`` -- what was asked, of whom, how agreement came out.

With ``anonymise`` (the default) annotator ids are replaced by stable codes
(A01, A02, ...). The code book lives only in the studio home
(``annotator_codes.json``), never in the export.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import __version__, paths
from .accounts import write_json_atomic
from .config import WorkspaceConfig
from .quality import answers, report


def codes_for(names: list[str]) -> dict[str, str]:
    path = paths.key_file()
    book = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    changed = False
    for name in sorted(set(names)):
        if name not in book:
            book[name] = f"A{len(book) + 1:02d}"
            changed = True
    if changed:
        write_json_atomic(path, book)
    return book


def export(ws: WorkspaceConfig, project_ids: list[str] | None = None, *,
           anonymise: bool = True, include_notes: bool = True,
           out_root: Path | None = None) -> Path:
    projects = [p for p in ws.projects if not project_ids or p.id in project_ids]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = (out_root or ws.exports_dir) / stamp
    out.mkdir(parents=True, exist_ok=True)

    frames, agreement, sections = [], [], []
    for p in projects:
        long = answers(ws, p)
        if long.empty:
            continue
        long.insert(0, "project", p.id)
        long.insert(1, "task", p.task)
        long["is_check_item"] = long["id"].isin({str(k) for k in p.checks})
        frames.append(long)
        rep = report(ws, p)
        agreement.append({"project": p.id, "task": p.task, "level": rep["level"],
                          "alpha": rep["alpha"], "items_with_2plus": rep["items_with_2plus"],
                          "answers": rep["answers"]})
        spec = p.task_spec
        if spec.kind == "labels":
            answer_desc = ", ".join(f"`{lab.value}` ({lab.text})" for lab in spec.labels)
        elif spec.kind == "rating":
            r = spec.rating
            answer_desc = (f"number from {r.minimum:g} ({r.left_label or 'low'}) to {r.maximum:g} "
                           f"({r.right_label or 'high'}) in steps of {r.step:g}"
                           + (f"; or `UNSURE` ({r.unsure_label})" if r.unsure_label else ""))
        else:
            answer_desc = "free text"
        alpha = "n/a" if rep["alpha"] is None else f"{rep['alpha']:.3f}"
        sections.append(
            f"### {p.name} (`{p.id}`)\n\n"
            f"- Task: `{p.task}`; answers: {answer_desc}\n"
            f"- Answers: {rep['answers']} from {long['annotator'].nunique()} annotators; "
            f"{rep['items_with_2plus']} items answered by two or more\n"
            f"- Krippendorff's alpha ({rep['level']}): {alpha}\n"
            + (f"- Check items: {len(p.checks)} (flagged `is_check_item`)\n" if p.checks else "")
            + (f"\n<details><summary>Instructions shown to annotators</summary>\n\n"
               f"{p.instructions}\n\n</details>\n" if p.instructions else "")
        )

    data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(
        columns=["project", "task", "annotator", "batch", "id", "value"])
    if anonymise and not data.empty:
        book = codes_for(data["annotator"].tolist())
        data["annotator"] = data["annotator"].map(book)
    if not include_notes and "note" in data.columns:
        data = data.drop(columns=["note"])
    data.to_csv(out / "answers.csv", index=False)
    data.to_json(out / "answers.jsonl", orient="records", lines=True, force_ascii=False)
    pd.DataFrame(agreement).to_csv(out / "agreement.csv", index=False)

    n_people = data["annotator"].nunique() if not data.empty else 0
    (out / "DATASHEET.md").write_text(
        f"# {ws.name} — annotation export\n\n"
        f"Exported {stamp} UTC with Labelwise {__version__}.\n\n"
        f"- Workspace: `{ws.id}` ({ws.description or 'no description'})\n"
        f"- Owner: {ws.owner or '—'}\n"
        f"- Answers: {len(data)} from {n_people} annotators\n"
        f"- Annotator ids: {'replaced by codes (A01, …); the code book is kept privately' if anonymise else 'real ids'}\n"
        f"- Notes: {'included as written — read them before publishing' if include_notes else 'removed'}\n\n"
        f"## Columns\n\n"
        f"`project`, `task`, `annotator`, `batch`, `id` (item), `value` (the answer), `note`, "
        f"`seconds` (time the item was open when answered; blank for answers recorded before Labelwise), "
        f"`updated_at` (UTC), `is_check_item`.\n\n"
        f"## Projects\n\n" + "\n".join(sections),
        encoding="utf-8",
    )
    return out
