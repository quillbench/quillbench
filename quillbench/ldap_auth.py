"""Optional sign-in against an institution's LDAP or Active Directory server.

Turned on by an ``ldap`` block in ``settings.yaml``::

    ldap:
      url: ldaps://ldap.example.edu          # ldap:// with start_tls: true also works
      user_dn: "uid={user},ou=people,dc=example,dc=edu"   # Active Directory: "{user}@example.edu"
      auto_create: true                       # first sign-in makes an annotator account
      ca_file: /path/to/ca.pem                # only when the server's certificate is from a private CA

The password is checked by binding to the LDAP server as that person; it is never
stored here. Needs the ``ldap3`` package: ``pip install "quillbench[ldap]"``.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

# LDAP user ids are restricted to plain characters: they are put into a DN, and
# they also become QuillBench user ids.
USER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def enabled(conf: dict | None) -> bool:
    return bool(conf and conf.get("url") and conf.get("user_dn"))


def check(name: str, password: str, conf: dict) -> bool:
    """True when the LDAP server accepts this user id and password."""
    # An empty password would be an anonymous bind, which many servers accept.
    if not password or not USER_RE.match(name):
        return False
    try:
        import ldap3
        from ldap3.utils.dn import escape_rdn
    except ImportError:
        log.error("LDAP sign-in is configured but ldap3 is not installed: "
                  "pip install 'quillbench[ldap]'")
        return False
    if str(conf["url"]).lower().startswith("ldap://") and not conf.get("start_tls"):
        log.error("LDAP sign-in refused: ldap:// without start_tls would send passwords "
                  "unencrypted. Use ldaps:// or set start_tls: true.")
        return False
    template = str(conf["user_dn"])
    user = name if "=" not in template else escape_rdn(name)
    timeout = int(conf.get("timeout", 10))
    import ssl

    # The password crosses the network to this server, so its certificate is always
    # checked (system CA list, or ``ca_file``); without this anyone on the path could
    # pose as the LDAP server and collect passwords.
    tls = ldap3.Tls(validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLS_CLIENT,
                    ca_certs_file=conf.get("ca_file") or None)
    try:
        server = ldap3.Server(str(conf["url"]), connect_timeout=timeout, tls=tls)
        conn = ldap3.Connection(server, user=template.format(user=user), password=password,
                                receive_timeout=timeout, raise_exceptions=False)
        if conf.get("start_tls"):
            conn.open()
            if not conn.start_tls():
                log.error("LDAP start_tls failed: %s", conn.result)
                return False
        ok = bool(conn.bind())
        conn.unbind()
        return ok
    except Exception as exc:  # an unreachable server must not crash the sign-in page
        log.error("LDAP sign-in failed for %s: %s", name, exc)
        return False
