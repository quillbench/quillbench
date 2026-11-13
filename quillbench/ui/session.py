"""Browser side of sign-in: the signed cookie that survives WebSocket reconnects."""

from __future__ import annotations

import json
import time

import streamlit as st

from .. import accounts
from .embed import run_script
from ..settings import StudioSettings


@st.cache_resource
def _secret() -> str:
    return accounts.app_secret()


def _js(value: str) -> str:
    return json.dumps(value)


# Streamlit sets the cookie from JS, so it cannot be HttpOnly; Secure is added
# whenever the page itself was served over HTTPS (the iframe is about:srcdoc, so
# the parent's protocol is the one that counts).
_SECURE = "(window.parent.location.protocol === 'https:' ? '; Secure' : '')"


def emit_auth_cookie(settings: StudioSettings, name: str, issued: int, users: dict) -> None:
    """Streamlit can read cookies but not set them, so writes go through a
    zero-height JS component."""
    payload = accounts.cookie_value(name, issued, users, _secret())
    max_age = max(0, settings.session_max_age_days * 24 * 3600 - (int(time.time()) - issued))
    run_script(
        "<script>document.cookie = "
        f"{_js(settings.cookie_name)} + '=' + encodeURIComponent({_js(payload)}) + "
        f"'; path=/; max-age={max_age}; SameSite=Lax' + {_SECURE};</script>",
        "cookie_set",
    )


def expire_auth_cookie(settings: StudioSettings) -> None:
    run_script(
        "<script>document.cookie = "
        f"{_js(settings.cookie_name)} + '=; path=/; max-age=0; SameSite=Lax' + {_SECURE};</script>",
        "cookie_clear",
    )


def restore_session(settings: StudioSettings, users: dict) -> tuple[str, int] | None:
    """``(user, issued)`` from a valid cookie, else None."""
    try:
        raw = st.context.cookies.get(settings.cookie_name, "")
    except Exception:
        return None
    return accounts.session_from_cookie(raw, users, _secret(),
                                        settings.session_max_age_days * 24 * 3600)


def client_info() -> dict:
    """Where a request came from, kept with each saved entry: IP address, browser,
    browser language and a random id for this browser tab's session."""
    import secrets

    sid = st.session_state.setdefault("qb_session_id", secrets.token_hex(6))
    try:
        ip = st.context.ip_address or ""
    except Exception:
        ip = ""
    try:
        headers = st.context.headers
        agent = headers.get("User-Agent", "") or ""
        lang = headers.get("Accept-Language", "") or ""
    except Exception:
        agent = lang = ""
    return {"ip": ip, "user_agent": agent[:300], "language": lang[:100], "session": sid}
