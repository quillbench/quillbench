"""
Annotation quality: agreement, check items and time per item.

* Krippendorff's alpha (nominal, ordinal or interval) over every item answered by
  two or more people; handles any number of annotators and missing answers.
* Cohen's kappa for each pair of annotators on the items both answered.
* Check items: a project's ``checks:`` map (item id -> expected answer) hidden
  among the real items. A rating check may be a number (within one scale step),
  a ``[low, high]`` range, or ``{value: 0.8, tolerance: 0.15}``.
* Median seconds per item, from the ``seconds`` column Labelwise records.

Skipped items, blanks and "UNSURE" answers are left out of agreement.
"""

from __future__ import annotations

from collections import Counter
from itertools import combinations

import pandas as pd

from .config import ProjectConfig, WorkspaceConfig
from .storage import progress_files


def answers(ws: WorkspaceConfig, project: ProjectConfig) -> pd.DataFrame:
    """Long table: annotator, batch, id, value, note, seconds, updated_at."""
    frames = []
    for pf in progress_files(ws):
        parts = pf.stem.split("__", 1)
        if len(parts) != 2:
            continue
        annotator, batch = parts
        try:
            if ws.project_for_batch(batch).id != project.id:
                continue
            if ws.progress_dir_for(project).resolve() != pf.parent.resolve():
                continue
            df = pd.read_csv(pf, dtype=str, keep_default_na=False)
        except Exception:
            continue
        if "human_verdict" not in df.columns or "id" not in df.columns:
            continue
        df = df[df["human_verdict"].apply(project.task_spec.is_labeled)]
        out = pd.DataFrame({
            "annotator": annotator, "batch": batch, "id": df["id"].astype(str),
            "value": df["human_verdict"].astype(str).str.strip(),
            "note": df.get("note", ""), "seconds": pd.to_numeric(df.get("seconds"), errors="coerce"),
            "updated_at": df.get("updated_at", ""),
        })
        frames.append(out)
    if not frames:
        return pd.DataFrame(columns=["annotator", "batch", "id", "value", "note", "seconds", "updated_at"])
    long = pd.concat(frames, ignore_index=True)
    # The same person answering the same item in two batches: keep the latest.
    return long.drop_duplicates(["annotator", "id"], keep="last").reset_index(drop=True)


def _numeric(project: ProjectConfig) -> bool:
    return project.task_spec.kind == "rating"


def krippendorff_alpha(units: dict, level: str = "nominal") -> float | None:
    """Alpha from ``{unit: [value, value, ...]}``. ``None`` if it is undefined."""
    units = {u: list(v) for u, v in units.items() if len(v) >= 2}
    if not units:
        return None
    values = [x for vs in units.values() for x in vs]
    n = len(values)
    counts = Counter(values)
    cats = sorted(counts)

    if level == "interval":
        def delta(a, b):
            return (float(a) - float(b)) ** 2
    elif level == "ordinal":
        cum: dict = {}
        order = sorted(cats, key=float) if all(_isnum(c) for c in cats) else cats
        idx = {c: i for i, c in enumerate(order)}
        prefix = [0]
        for c in order:
            prefix.append(prefix[-1] + counts[c])

        def delta(a, b):
            i, j = sorted((idx[a], idx[b]))
            s = prefix[j + 1] - prefix[i] - (counts[a] + counts[b]) / 2
            return s * s
        del cum
    else:
        def delta(a, b):
            return 0.0 if a == b else 1.0

    d_o = 0.0
    for vs in units.values():
        m = len(vs)
        d_o += sum(delta(a, b) for a, b in combinations(vs, 2)) * 2 / (m - 1)
    d_o /= n
    d_e = sum(counts[a] * counts[b] * delta(a, b) for a in cats for b in cats if a != b)
    d_e /= n * (n - 1)
    if d_e == 0:
        return None
    return 1 - d_o / d_e


def _isnum(x) -> bool:
    try:
        float(x)
        return True
    except (TypeError, ValueError):
        return False


def cohen_kappa(a: list, b: list) -> float | None:
    if len(a) != len(b) or len(a) < 2:
        return None
    n = len(a)
    p_o = sum(x == y for x, y in zip(a, b)) / n
    ca, cb = Counter(a), Counter(b)
    p_e = sum(ca[k] * cb.get(k, 0) for k in ca) / (n * n)
    if p_e == 1:
        return None
    return (p_o - p_e) / (1 - p_e)


def _check_ok(project: ProjectConfig, value: str, expected) -> bool:
    spec = project.task_spec
    if spec.kind != "rating" or value == "UNSURE":
        return str(value) == str(expected)
    v = float(value)
    if isinstance(expected, (list, tuple)) and len(expected) == 2:
        return float(expected[0]) <= v <= float(expected[1])
    if isinstance(expected, dict):
        return abs(v - float(expected["value"])) <= float(expected.get("tolerance", spec.rating.step))
    return abs(v - float(expected)) <= spec.rating.step + 1e-9


def report(ws: WorkspaceConfig, project: ProjectConfig) -> dict:
    long = answers(ws, project)
    spec = project.task_spec
    level = spec.agreement_level if spec.kind != "text" else None
    checks = {str(k): v for k, v in project.checks.items()}
    real = long[~long["id"].isin(checks)]
    usable = real[real["value"] != "UNSURE"]

    alpha = None
    if level and not usable.empty:
        units = usable.groupby("id")["value"].apply(list).to_dict()
        alpha = krippendorff_alpha(units, level)

    pairs = []
    if spec.kind == "labels" and not usable.empty:
        wide = usable.pivot_table(index="id", columns="annotator", values="value", aggfunc="first")
        for x, y in combinations(wide.columns, 2):
            both = wide[[x, y]].dropna()
            if len(both) >= 2:
                k = cohen_kappa(both[x].tolist(), both[y].tolist())
                pairs.append({"Pair": f"{x} · {y}", "Shared items": len(both),
                              "Agreement %": round(100 * (both[x] == both[y]).mean(), 1),
                              "Cohen κ": None if k is None else round(k, 3)})
    elif spec.kind == "rating" and not usable.empty:
        num = usable.assign(v=pd.to_numeric(usable["value"], errors="coerce"))
        wide = num.pivot_table(index="id", columns="annotator", values="v", aggfunc="first")
        for x, y in combinations(wide.columns, 2):
            both = wide[[x, y]].dropna()
            if len(both) >= 2:
                pairs.append({"Pair": f"{x} · {y}", "Shared items": len(both),
                              "Mean |difference|": round((both[x] - both[y]).abs().mean(), 3),
                              "Pearson r": round(both[x].corr(both[y]), 3) if both[x].std() and both[y].std() else None})

    people = []
    for name, g in long.groupby("annotator"):
        row = {"Annotator": name, "Answered": int((~g["id"].isin(checks)).sum()),
               "Median s/item": round(float(g["seconds"].median()), 1) if g["seconds"].notna().any() else None}
        if spec.kind == "labels":
            row["Most used"] = g["value"].value_counts().idxmax()
            row["Share of most used %"] = round(100 * g["value"].value_counts(normalize=True).iloc[0], 1)
        elif spec.kind == "rating":
            nums = pd.to_numeric(g["value"], errors="coerce")
            row["Mean"] = round(float(nums.mean()), 3) if nums.notna().any() else None
            row["Unsure %"] = round(100 * (g["value"] == "UNSURE").mean(), 1)
        if checks:
            ch = g[g["id"].isin(checks)]
            row["Checks passed"] = (f"{sum(_check_ok(project, v, checks[i]) for i, v in zip(ch['id'], ch['value']))}"
                                    f"/{len(ch)}")
        people.append(row)

    multi = usable.groupby("id")["annotator"].nunique()
    return {
        "project": project.name,
        "level": level,
        "alpha": alpha,
        "items_with_2plus": int((multi >= 2).sum()),
        "answers": int(len(long)),
        "pairs": pairs,
        "people": people,
    }
