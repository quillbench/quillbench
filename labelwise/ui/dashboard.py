"""Progress and quality. Annotators see only their own rows; admins see everyone,
plus agreement, check items and time per item."""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from ..config import WorkspaceConfig
from ..quality import report
from ..storage import progress_summary_rows
from .theme import progress_chip


def _my_progress(rows: list[dict]) -> None:
    """An annotator's own batches, one card each. No one else's numbers are shown:
    seeing a colleague's pace or answer mix would bias the work."""
    done = sum(r["Labeled"] for r in rows)
    total = sum(r["Total"] for r in rows)
    progress_chip(done, total)
    for r in sorted(rows, key=lambda r: (r["%"] == 100, r["Batch"])):
        with st.container(border=True):
            c1, c2 = st.columns([2, 3], vertical_alignment="center")
            c1.markdown(f"**{html.escape(str(r['Batch']))}**  \n<span style='color:var(--muted);font-size:.8rem'>"
                        f"{html.escape(str(r['Project']))}</span>", unsafe_allow_html=True)
            with c2:
                progress_chip(r["Labeled"], r["Total"])


def render(ws: WorkspaceConfig, me: str, is_admin: bool) -> None:
    c0, c1 = st.columns([4, 1])
    c0.subheader("Progress")
    if c1.button("Refresh", width="stretch"):
        st.rerun()

    rows, problems = progress_summary_rows(ws)
    if problems and is_admin:
        # Surfaced, not swallowed: an unreadable file is missing from every total below.
        st.warning("Some progress files could not be read and are left out of the figures "
                   "below:\n\n" + "\n".join(f"- {p}" for p in problems))
    if not is_admin:
        rows = [r for r in rows if r["Annotator"] == me]
    if not rows:
        st.info("No answers have been recorded yet.")
        return
    if not is_admin:
        _my_progress(rows)
        return

    df = pd.DataFrame(rows).drop(columns=["_file", "_project"])
    labeled, total = int(df["Labeled"].sum()), int(df["Total"].sum())
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Overall completion", f"{round(labeled / total * 100) if total else 0}%")
    m2.metric("Items answered", f"{labeled:,}")
    m3.metric("Annotators", df["Annotator"].nunique())
    secs = df["Median s/item"].dropna()
    m4.metric("Median s/item", f"{secs.median():.0f}" if len(secs) else "—")

    projects = ["All projects"] + sorted(df["Project"].unique())
    pick = st.selectbox("Project", projects, key=f"dash_proj_{ws.id}")
    view = df if pick == "All projects" else df[df["Project"] == pick]
    st.dataframe(view, width="stretch", hide_index=True,
                 column_config={"%": st.column_config.ProgressColumn("%", min_value=0, max_value=100,
                                                                     format="%d%%")})

    if not is_admin:
        return
    st.divider()
    st.subheader("Quality")
    st.caption("Krippendorff's alpha over items answered by two or more people (skips and "
               "'unsure' left out). Above 0.8 is good; 0.667–0.8 allows tentative conclusions.")
    names = {p.name: p for p in ws.projects}
    chosen = [names[pick]] if pick in names else [p for p in ws.projects
                                                 if p.name in set(df["Project"])]
    for p in chosen:
        rep = report(ws, p)
        alpha = "n/a" if rep["alpha"] is None else f"{rep['alpha']:.3f}"
        with st.expander(f"{p.name} — α ({rep['level'] or '—'}) = {alpha} · "
                         f"{rep['items_with_2plus']} items with 2+ answers",
                         expanded=len(chosen) == 1):
            if rep["people"]:
                st.markdown("**Per annotator**")
                st.dataframe(pd.DataFrame(rep["people"]), width="stretch", hide_index=True)
            if rep["pairs"]:
                st.markdown("**Pairs**")
                st.dataframe(pd.DataFrame(rep["pairs"]), width="stretch", hide_index=True)
            if not p.checks:
                st.caption("No check items in this project. Add `checks: {item_id: expected}` "
                           "to its manifest entry to measure attention.")
