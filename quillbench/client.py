"""For annotators: remember a QuillBench server and open it.

    quillbench connect annotate.example.edu/quillbench
    quillbench open

The address is kept in ``~/.config/quillbench/client.yaml``. Answers are saved on the
server, never on this computer.
"""

from __future__ import annotations

import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import yaml


def client_file() -> Path:
    return Path.home() / ".config" / "quillbench" / "client.yaml"


def _reachable(url: str, timeout: float = 6) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/_stcore/health", timeout=timeout) as r:
            return r.status == 200 and r.read(16).strip() == b"ok"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def find_server(address: str) -> str:
    """The full address of a running QuillBench server, or a ValueError saying why not."""
    address = address.strip().rstrip("/")
    if not address:
        raise ValueError("Give the server's address, for example annotate.example.edu/quillbench")
    if "://" in address:
        candidates = [address]
    else:
        candidates = [f"https://{address}", f"http://{address}"]
    for url in candidates:
        if not url.startswith(("https://", "http://")):
            raise ValueError("The address must start with https:// or http://")
        if _reachable(url):
            return url + "/"
    raise ValueError(f"No QuillBench server answered at {address}. Check the address "
                     "(including any :port and path) with whoever runs the server.")


def save(url: str) -> None:
    path = client_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"server": url}), encoding="utf-8")


def saved() -> str | None:
    path = client_file()
    if not path.exists():
        return None
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("server")


def open_server(url: str) -> None:
    if not webbrowser.open(url):
        print(f"Open this address in your browser: {url}")
