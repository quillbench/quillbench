"""
Look and feel of Labelwise: two palettes, the stylesheet, and the
small HTML pieces (top bar, sign-in card, progress chip) the screens share.

Colours are CSS custom properties, so every rule is written once and the light
and dark palettes switch wholesale. Answer buttons are coloured by what the
answer *means* (good / bad / warn / neutral, from the task definition), never by
their position in the row -- position-based colouring painted "Can't judge" red
in the rating task and nudged labellers in the blinded A/B task.

Load-bearing details:
* the icon-font restore after the blanket font rule (without it, icons render
  as their ligature names, e.g. the text "arrow_light");
* result tables use ``st.table``, not ``st.dataframe`` (see query_review.py).
"""

from __future__ import annotations

import base64
from pathlib import Path

import html
from string import Template

import streamlit as st

PALETTES = {
    "light": dict(
        scheme="light",
        bg="#F5F6FA", surface="#FFFFFF", surface2="#EEF1F6", sidebar="#FFFFFF",
        ink="#1D2433", muted="#5B6576", line="#DDE2EA", heading="#131B2E",
        accent="#2B3A67", accent_soft="#5468A8", accent_bg="#E8ECF8", mark="#D9962B",
        green="#1E7A52", green_bg="#E6F4EC", red="#B03A3A", red_bg="#FBEAEA",
        amber="#A86B12", amber_bg="#FCF1DF", neutral="#4D586A", neutral_bg="#EEF1F6",
        shadow="rgba(20, 28, 46, 0.08)",
    ),
    "dark": dict(
        scheme="dark",
        bg="#0F141C", surface="#171E29", surface2="#1F2836", sidebar="#121821",
        ink="#DCE3EC", muted="#94A1B3", line="#2A3443", heading="#F0F3F8",
        accent="#8394DC", accent_soft="#A3B1EA", accent_bg="#232C45", mark="#F0B95A",
        green="#52B98D", green_bg="#16302A", red="#E58080", red_bg="#3A1F24",
        amber="#E4AE58", amber_bg="#372B17", neutral="#AAB5C5", neutral_bg="#232C3A",
        shadow="rgba(0, 0, 0, 0.35)",
    ),
}

_CSS = Template("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap');
:root {
    color-scheme: $scheme;
    --bg: $bg; --surface: $surface; --surface2: $surface2; --sidebar-bg: $sidebar;
    --ink: $ink; --muted: $muted; --line: $line; --heading: $heading;
    --accent: $accent; --accent-soft: $accent_soft; --accent-bg: $accent_bg; --mark: $mark;
    --green: $green; --green-bg: $green_bg; --red: $red; --red-bg: $red_bg;
    --amber: $amber; --amber-bg: $amber_bg; --neutral: $neutral; --neutral-bg: $neutral_bg;
    --shadow: $shadow;
    --serif: 'Source Serif 4', Georgia, 'Times New Roman', serif;
    --sans:  Inter, 'Segoe UI', 'Helvetica Neue', Helvetica, Arial, sans-serif;
    --mono:  ui-monospace, SFMono-Regular, Consolas, 'Liberation Mono', Menlo, monospace;
    --radius: 10px;
}

/* Streamlit chrome we do not want in a working tool */
/* Hide Deploy and the developer menu, but NOT the whole toolbar: the button that
   reopens a closed sidebar lives inside it, and hiding the toolbar left people
   unable to get the sidebar back. */
[data-testid="stDecoration"], [data-testid="stAppDeployButton"], [data-testid="stMainMenu"],
[data-testid="stToolbarActions"] { display: none !important; }
[data-testid="stExpandSidebarButton"] {
    background: var(--surface) !important; border: 1px solid var(--line) !important;
    border-radius: 8px !important; box-shadow: 0 2px 8px var(--shadow);
}
[data-testid="stExpandSidebarButton"] * { color: var(--heading) !important; }
#MainMenu, footer { visibility: hidden; }
[data-testid="stHeader"] { background: transparent; height: 2.4rem; }
.block-container { padding-top: 0.9rem; padding-bottom: 5rem; max-width: 1280px; }

/* Base surfaces and text -- enforced in both modes so no browser or server
   theme can produce an illegible combination. */
[data-testid="stAppViewContainer"] { background: var(--bg) !important; color: var(--ink) !important; }
[data-testid="stAppViewContainer"] p, [data-testid="stAppViewContainer"] li,
[data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"],
[data-testid="stAppViewContainer"] [data-testid="stWidgetLabel"] p { color: var(--ink); }
[data-testid="stAppViewContainer"] [data-testid="stCaptionContainer"] p { color: var(--muted); }
[data-testid="stSidebar"] p, [data-testid="stSidebar"] label,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] { color: var(--ink); }

html, body, [data-testid="stAppViewContainer"] *, [data-testid="stSidebar"] * { font-family: var(--sans); }
/* Restore the icon font after the blanket rule above, or icons render as their
   ligature names ("arrow_light") over nearby labels. */
[data-testid="stIconMaterial"], [data-testid^="stIcon"], [class*="material-symbols"] {
    font-family: "Material Symbols Rounded" !important;
}
[data-testid="stAppViewContainer"] pre, [data-testid="stAppViewContainer"] pre *,
[data-testid="stAppViewContainer"] code, [data-testid="stAppViewContainer"] code * {
    font-family: var(--mono) !important;
}
h1, h2, h3 { font-family: var(--serif) !important; color: var(--heading) !important;
             font-weight: 600 !important; letter-spacing: -0.005em; }
h1 { font-size: 1.5rem !important; }
h2 { font-size: 1.25rem !important; }
h3 { font-size: 1.05rem !important; }
[data-testid="stMarkdownContainer"] :not(pre) > code {
    background: var(--surface2); color: var(--heading); padding: 0.05rem 0.32rem; border-radius: 4px;
}

/* ── Top bar ─────────────────────────────────────────────────────────── */
.labelwise-top {
    display: flex; align-items: center; gap: 0.9rem; flex-wrap: wrap;
    padding: 0.55rem 0.2rem 0.85rem; margin-bottom: 0.4rem; border-bottom: 1px solid var(--line);
}
.labelwise-mark {
    width: 2.15rem; height: 2.15rem; border-radius: 8px; flex: none;
    display: block;
}
.labelwise-name { font-family: var(--serif); font-size: 1.12rem; font-weight: 600; color: var(--heading); line-height: 1.1; }
.labelwise-name small { display: block; font: 500 0.72rem/1.4 var(--sans); color: var(--muted); letter-spacing: 0.02em; }
.labelwise-top .spacer { flex: 1; }
.labelwise-chip { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.28rem 0.7rem;
            border: 1px solid var(--line); border-radius: 999px; background: var(--surface);
            font-size: 0.8rem; color: var(--ink); white-space: nowrap; }
.labelwise-chip b { color: var(--heading); font-weight: 600; }
.labelwise-chip.ws { background: var(--accent-bg); border-color: transparent; color: var(--accent); }

/* ── Sign-in card ───────────────────────────────────────────────────── */
.st-key-labelwise_login {
    max-width: 400px; margin: 7vh auto 0; padding: 2rem 2rem 1.4rem;
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
    box-shadow: 0 12px 32px var(--shadow);
}
.labelwise-login-head { text-align: center; margin-bottom: 1.1rem; }
.labelwise-login-head .labelwise-mark { width: 3rem; height: 3rem; margin: 0 auto 0.8rem; border-radius: 11px; }
.labelwise-login-head h1 { margin: 0; padding: 0; font-size: 1.35rem !important; }
.labelwise-login-head p { margin: 0.25rem 0 0; color: var(--muted) !important; font-size: 0.86rem; }
.labelwise-login-foot { text-align: center; color: var(--muted); font-size: 0.74rem; margin-top: 1rem; }

/* ── Sidebar ────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] { background: var(--sidebar-bg); border-right: 1px solid var(--line); }
.sidebar-user { font-weight: 600; color: var(--heading); margin: 0; }
.sidebar-role { font-size: 0.78rem; color: var(--muted); margin: 0 0 0.6rem; }
.sidebar-footnote { font-size: 0.72rem; color: var(--muted); line-height: 1.6; }

/* ── Widgets ────────────────────────────────────────────────────────── */
.stButton > button, [data-testid="stPopover"] button, .stDownloadButton > button {
    border-radius: 8px !important; font-weight: 500;
}
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
    background: var(--accent) !important; border-color: var(--accent) !important; color: #fff !important;
}
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-primaryFormSubmit"] p { color: #fff !important; }
[data-testid="stExpander"] details { border-radius: var(--radius) !important; border-color: var(--line) !important; background: var(--surface); }
.stTabs [data-baseweb="tab-list"] { gap: 0.4rem; border-bottom: 1px solid var(--line); }
.stTabs [data-baseweb="tab"] { padding: 0.45rem 0.9rem; font-weight: 500; }
.stTabs [aria-selected="true"] p { color: var(--accent) !important; font-weight: 600; }
[data-testid="stMetric"] { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 0.7rem 0.9rem; }
[data-testid="stTable"] table { border-color: var(--line) !important; background: transparent !important; }
[data-testid="stTable"] th { color: var(--muted) !important; font-weight: 600; }
[data-testid="stTable"] tbody tr:nth-child(odd) td { background: var(--surface2) !important; }

/* ── Work bar: batch, progress, instructions, settings ──────────────── */
.labelwise-progress { display: flex; flex-direction: column; gap: 0.3rem; padding-top: 0.2rem; }
.labelwise-progress .row { display: flex; justify-content: space-between; font-size: 0.8rem; color: var(--muted); }
.labelwise-progress .row b { color: var(--heading); font-weight: 600; }
.labelwise-progress .track { height: 8px; border-radius: 999px; background: var(--surface2); overflow: hidden; }
.labelwise-progress .fill { height: 100%; border-radius: 999px; background: var(--accent); }
.labelwise-progress .fill.done { background: var(--green); }

/* ── Item card ──────────────────────────────────────────────────────── */
.st-key-labelwise_item {
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
    padding: 1rem 1.2rem 0.6rem; box-shadow: 0 2px 10px var(--shadow);
}
.meta-badge { display: inline-block; background: var(--surface2); color: var(--ink); border-radius: 999px;
              padding: 0.14rem 0.62rem; font-size: 0.72rem; font-weight: 600; margin: 0 0.35rem 0.35rem 0; }
.meta-badge.primary { background: var(--accent); color: #fff; }
.meta-badge.subtle { background: transparent; border: 1px solid var(--line); color: var(--muted); font-weight: 500; }
.meta-badge.done { background: var(--green-bg); color: var(--green); }
.nl-question {
    font-family: var(--serif); font-size: 1.28rem; line-height: 1.5; color: var(--heading);
    background: transparent; border-left: 4px solid var(--mark); padding: 0.35rem 0 0.35rem 1rem; margin: 0.4rem 0 0.9rem;
}
.field-label { font-size: 0.74rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
               color: var(--muted); margin: 0.6rem 0 0.25rem; }
.field-text { font-size: 1rem; line-height: 1.6; color: var(--ink); margin-bottom: 0.4rem; }
.st-key-labelwise_item [data-testid="stTextArea"] { margin-top: 0.5rem; }
.nav-legend { font-size: 0.78rem; color: var(--muted); line-height: 1.9; }
.kbd { display: inline-block; min-width: 1.4em; padding: 0 0.35em; border: 1px solid var(--line); border-bottom-width: 2px;
       border-radius: 5px; background: var(--surface); font: 600 0.75rem/1.5 var(--mono); text-align: center; color: var(--heading); }

/* ── Answer bar: stays on screen while the item scrolls ─────────────── */
/* Streamlit wraps the keyed container in a layer of exactly its own height, so the
   sticky rule has to sit on that wrapper or the bar has no room to stick. */
@media (min-width: 641px) {
    [data-testid="stLayoutWrapper"]:has(> .st-key-labelwise_answerbar) { position: sticky; bottom: 0.5rem; z-index: 20; }
}
.st-key-labelwise_answerbar {
    margin-top: 0.6rem;
    background: var(--surface); border: 1px solid var(--line); border-radius: 14px;
    padding: 0.75rem 0.9rem 0.55rem; box-shadow: 0 -6px 22px var(--shadow);
}
.answer-head { display: flex; justify-content: space-between; align-items: baseline; gap: 1rem; margin-bottom: 0.15rem; }
.answer-head .label { font-size: 0.74rem; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--muted); }
.answer-head .recorded { font-size: 0.8rem; color: var(--muted); }
.answer-head .recorded b { color: var(--heading); }

[class*="st-key-verdict_"] button { min-height: 2.75rem; border-width: 1.5px !important; font-weight: 600 !important;
                                    background: var(--surface) !important; }
[class*="st-key-verdict_"] button p { font-weight: 600 !important; }
[class*="st-key-verdict_"][class*="_xgood"] button { border-color: var(--green) !important; }
[class*="st-key-verdict_"][class*="_xgood"] button p { color: var(--green) !important; }
[class*="st-key-verdict_"][class*="_xbad"] button { border-color: var(--red) !important; }
[class*="st-key-verdict_"][class*="_xbad"] button p { color: var(--red) !important; }
[class*="st-key-verdict_"][class*="_xwarn"] button { border-color: var(--amber) !important; }
[class*="st-key-verdict_"][class*="_xwarn"] button p { color: var(--amber) !important; }
[class*="st-key-verdict_"][class*="_xneu"] button { border-color: var(--line) !important; }
[class*="st-key-verdict_"][class*="_xneu"] button p { color: var(--neutral) !important; }
[class*="st-key-verdict_"][class*="_xgoodS_"] button { background: var(--green) !important; }
[class*="st-key-verdict_"][class*="_xbadS_"] button { background: var(--red) !important; }
[class*="st-key-verdict_"][class*="_xwarnS_"] button { background: var(--amber) !important; }
[class*="st-key-verdict_"][class*="_xneuS_"] button { background: var(--neutral) !important; border-color: var(--neutral) !important; }
[class*="st-key-verdict_"][class*="S_"][class*="_x"] button p { color: #fff !important; }
[class*="st-key-verdict_skip_"] button { border-style: dashed !important; border-color: var(--line) !important; }
[class*="st-key-verdict_skip_"] button p { color: var(--muted) !important; font-weight: 500 !important; }
[class*="st-key-verdict_"] button:hover, [class*="st-key-rating_save_"] button:hover { filter: brightness(1.06); transform: translateY(-1px); }
[class*="st-key-rating_save_"] button { min-height: 2.75rem; }

/* Rating read-out and word marks (the slider's own end labels would repeat them) */
.st-key-labelwise_answerbar [data-testid="stSliderTickBar"],
.st-key-labelwise_answerbar [data-testid="stSliderTickBarMin"],
.st-key-labelwise_answerbar [data-testid="stSliderTickBarMax"],
.st-key-labelwise_answerbar [data-testid="stSliderThumbValue"] { display: none; }
[data-testid="stBaseButton-primary"]:disabled { opacity: 0.4; cursor: not-allowed; }
.rating-readout { display: flex; align-items: baseline; gap: 0.7rem; margin: 0.1rem 0 -0.2rem; }
.rating-readout .num { font: 700 2.1rem/1 var(--sans); color: var(--accent); font-variant-numeric: tabular-nums; }
.rating-readout .num.unset { color: var(--muted); }
.rating-readout .word { font-family: var(--serif); font-size: 1.05rem; color: var(--heading); }
.rating-marks { position: relative; height: 2.6rem; margin: -0.55rem 0.55rem 0.2rem; }
.rating-marks span { position: absolute; transform: translateX(-50%); top: 0; text-align: center;
                     font-size: 0.72rem; color: var(--muted); line-height: 1.25; max-width: 7.5rem; }
.rating-marks span::before { content: ""; display: block; width: 1px; height: 6px; margin: 0 auto 3px; background: var(--line); }
.rating-marks span.first { transform: none; text-align: left; } .rating-marks span.first::before { margin-left: 0; }
.rating-marks span.last { transform: translateX(-100%); text-align: right; } .rating-marks span.last::before { margin-right: 0; }

/* Feedback (practice) and completion cards */
.labelwise-card { border-radius: 12px; padding: 0.9rem 1.1rem; margin: 0.4rem 0 0.6rem; border: 1px solid var(--line); background: var(--surface); }
.labelwise-card.good { background: var(--green-bg); border-color: transparent; }
.labelwise-card.bad { background: var(--red-bg); border-color: transparent; }
.labelwise-card.done { background: var(--green-bg); border-color: transparent; }
.labelwise-card .title { font-weight: 700; color: var(--heading); margin-bottom: 0.2rem; }
.labelwise-card .body { color: var(--ink); font-size: 0.92rem; line-height: 1.55; }

/* Gate/collision call-outs keep Streamlit's alert styling */
[data-testid="stAlert"] { border-radius: var(--radius) !important; }

/* Hidden helper iframes (cookie writer, keyboard shortcuts) take no space;
   scripts in a display:none iframe still run. */
[class*="st-key-pashidden_"] { display: none !important; }

/* ── Phones ─────────────────────────────────────────────────────────── */
@media (max-width: 640px) {
    .block-container { padding: 0.6rem 0.7rem 6rem !important; }
    .labelwise-name small, .labelwise-top .spacer { display: none; }
    [data-testid="stHeader"] { height: 2rem; }
    .labelwise-mark { width: 1.8rem; height: 1.8rem; }
    .labelwise-name { font-size: 1rem; }
    .labelwise-chip { padding: 0.18rem 0.55rem; font-size: 0.74rem; }
    .labelwise-top { gap: 0.5rem; padding-bottom: 0.6rem; }
    .st-key-labelwise_item { padding: 0.8rem 0.85rem 0.4rem; border-radius: 12px; }
    .nl-question { font-size: 1.08rem; padding-left: 0.75rem; }
    .st-key-labelwise_login { margin-top: 2vh; padding: 1.5rem 1.2rem 1rem; }
    /* Work bar: batch and progress full width, Instructions and Settings side by side */
    .st-key-labelwise_workbar [data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: wrap !important; gap: 0.5rem !important; }
    .st-key-labelwise_workbar [data-testid="stColumn"] { min-width: calc(50% - 0.25rem) !important; width: calc(50% - 0.25rem) !important; flex: 1 1 calc(50% - 0.25rem) !important; }
    .st-key-labelwise_workbar [data-testid="stColumn"]:nth-child(-n+2) { min-width: 100% !important; width: 100% !important; flex-basis: 100% !important; }
    /* Item navigation: jump box on its own line, Previous and Next side by side */
    .st-key-labelwise_nav [data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: wrap !important; gap: 0.5rem !important; }
    .st-key-labelwise_nav [data-testid="stColumn"] { min-width: calc(50% - 0.25rem) !important; width: calc(50% - 0.25rem) !important; flex: 1 1 calc(50% - 0.25rem) !important; }
    .st-key-labelwise_nav [data-testid="stColumn"]:nth-child(2) { order: -1; min-width: 100% !important; width: 100% !important; flex-basis: 100% !important; }
    .labelwise-progress { margin-bottom: 0.5rem; }
    .rating-marks span.minor { display: none; }
    /* Answer buttons two per row rather than one per row */
    .st-key-labelwise_answerbar { padding: 0.6rem 0.7rem 0.4rem; }
    .st-key-labelwise_answerbar [data-testid="stHorizontalBlock"] { flex-direction: row !important; flex-wrap: wrap !important; gap: 0.4rem !important; }
    .st-key-labelwise_answerbar [data-testid="stColumn"] { min-width: calc(50% - 0.2rem) !important; width: calc(50% - 0.2rem) !important; flex: 1 1 calc(50% - 0.2rem) !important; }
    [class*="st-key-verdict_"] button, [class*="st-key-rating_save_"] button { min-height: 3rem; }
    .rating-readout .num { font-size: 1.8rem; }
}
$mode_extra
</style>
""")

_DARK_EXTRA = """
[data-testid="stAlert"] { background-color: var(--surface2) !important; border: 1px solid var(--line); }
[data-testid="stAlert"] p { color: var(--ink) !important; }
[data-testid="stDataFrame"], [data-testid="stCodeBlock"] { box-shadow: 0 0 0 1px var(--line); }
* { scrollbar-color: var(--line) var(--bg); scrollbar-width: thin; }
/* The server theme is light, so every widget surface is re-coloured here. */
.stButton > button, [data-testid="stPopover"] button, .stDownloadButton > button,
[data-testid="stFormSubmitButton"] button {
    background: var(--surface) !important; color: var(--ink) !important; border-color: var(--line) !important;
}
.stButton > button p, [data-testid="stPopover"] button p, [data-testid="stPopover"] button span { color: var(--ink) !important; }
.stButton > button:disabled { background: transparent !important; opacity: 0.45; }
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="input"] > div,
[data-baseweb="textarea"], [data-baseweb="textarea"] > div, textarea, input {
    background: var(--surface2) !important; color: var(--ink) !important; border-color: var(--line) !important;
}
[data-baseweb="select"] > div * { background: transparent !important; }
[data-baseweb="select"] *, textarea::placeholder, input::placeholder { color: var(--ink) !important; }
textarea::placeholder, input::placeholder { opacity: 0.55; }
[data-baseweb="popover"] ul, [data-baseweb="popover"] li, [data-baseweb="menu"],
[data-testid="stPopoverBody"], [data-baseweb="popover"] > div { background: var(--surface) !important; color: var(--ink) !important; }
[data-baseweb="popover"] li:hover, [data-baseweb="popover"] li[aria-selected="true"] { background: var(--surface2) !important; }
[data-testid="stExpander"] details, [data-testid="stExpander"] summary { background: var(--surface) !important; color: var(--ink) !important; }
[data-testid="stExpander"] summary p, [data-testid="stExpander"] summary span { color: var(--ink) !important; }
[data-testid="stTooltipContent"] { background: var(--surface2) !important; color: var(--ink) !important; }
[data-testid="stTable"] td, [data-testid="stTable"] th { color: var(--ink) !important; }
"""


def _logo() -> str:
    data = base64.b64encode((Path(__file__).parent / "assets" / "logo.svg").read_bytes()).decode()
    return f'<img class="labelwise-mark" alt="Labelwise" src="data:image/svg+xml;base64,{data}">'


def inject_css() -> None:
    palette = PALETTES["dark" if st.session_state.get("dark_mode", False) else "light"]
    st.markdown(_CSS.substitute(mode_extra=_DARK_EXTRA if palette["scheme"] == "dark" else "",
                                **palette), unsafe_allow_html=True)


def top_bar(title: str, subtitle: str, workspace: str = "", user: str = "") -> None:
    chips = ""
    if workspace:
        chips += f'<span class="labelwise-chip ws">{html.escape(workspace)}</span>'
    if user:
        chips += f'<span class="labelwise-chip">● <b>{html.escape(user)}</b></span>'
    # The mark already reads "Labelwise"; repeating the title beside it would say it twice.
    name = "" if title.strip().upper() == "Labelwise" else html.escape(title)
    st.markdown(
        f'<div class="labelwise-top">{_logo()}'
        f'<div class="labelwise-name">{name or html.escape(subtitle)}'
        f'{f"<small>{html.escape(subtitle)}</small>" if name else ""}</div>'
        f'<div class="spacer"></div>{chips}</div>',
        unsafe_allow_html=True,
    )


def login_head(title: str, subtitle: str) -> None:
    st.markdown(
        f'<div class="labelwise-login-head">{_logo()}'
        f'<h1>Sign in to {html.escape(title)}</h1><p>{html.escape(subtitle)}</p></div>',
        unsafe_allow_html=True,
    )


def progress_chip(done: int, total: int) -> None:
    pct = round(done / total * 100) if total else 0
    st.markdown(
        f'<div class="labelwise-progress"><div class="row"><span>Progress</span>'
        f'<span><b>{done}</b> / {total} · {pct}%</span></div>'
        f'<div class="track"><div class="fill{" done" if pct == 100 else ""}" '
        f'style="width:{pct}%"></div></div></div>',
        unsafe_allow_html=True,
    )


def card(kind: str, title: str, body: str = "") -> None:
    """``body`` is trusted HTML built by the caller (escape item text first)."""
    st.markdown(f'<div class="labelwise-card {kind}"><div class="title">{title}</div>'
                f'<div class="body">{body}</div></div>', unsafe_allow_html=True)
