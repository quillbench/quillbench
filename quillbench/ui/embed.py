"""Run a small trusted script in the page without showing anything.

Streamlit can read cookies but not set them, and has no keyboard-shortcut API,
so both go through a tiny iframe. Only fixed scripts written in this package
are passed here -- never user or item content.
"""

from __future__ import annotations

import streamlit as st


def run_script(html: str, key: str) -> None:
    with st.container(key=f"pashidden_{key}"):
        st.iframe(html, height=1)
