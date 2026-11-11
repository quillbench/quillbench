"""Detecting the database type from a host and port, against small fake servers."""

import socket
import threading

import pytest
from streamlit.testing.v1 import AppTest

from quillbench import accounts, connections
from quillbench.connections import detect, split_address

from test_app import APP, _login


def _mysql_greeting(version: bytes) -> bytes:
    body = b"\x0a" + version + b"\x00" + b"\x01\x00\x00\x00" + b"abcdefgh\x00" + b"\xff\xf7"
    return len(body).to_bytes(3, "little") + b"\x00" + body


def fake_server(on_connect):
    """Listen on a free local port; run ``on_connect(sock)`` for each connection."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen()

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                try:
                    on_connect(conn)
                except OSError:
                    pass

    threading.Thread(target=loop, daemon=True).start()
    return srv


@pytest.fixture
def serve():
    servers = []

    def start(on_connect):
        srv = fake_server(on_connect)
        servers.append(srv)
        return srv.getsockname()[1]

    yield start
    for s in servers:
        s.close()


def _postgres(conn):
    if conn.recv(8) == b"\x00\x00\x00\x08\x04\xd2\x16\x2f":
        conn.sendall(b"N")


def _neo4j(conn):
    data = conn.recv(20)
    if data[:4] == bytes.fromhex("6060B017"):
        conn.sendall(b"\x00\x00\x04\x05")


def _http_neo4j(conn):
    conn.recv(1024)
    conn.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n\r\n'
                 b'{"bolt_direct": "bolt://localhost:7687", "neo4j_version": "5.20.0"}')


def _http_plain(conn):
    conn.recv(1024)
    conn.sendall(b"HTTP/1.1 200 OK\r\n\r\n<html>hello</html>")


@pytest.mark.parametrize("handler, engine, words", [
    (lambda c: c.sendall(_mysql_greeting(b"8.0.36")), "mysql", "MySQL 8.0.36"),
    (lambda c: c.sendall(_mysql_greeting(b"5.5.5-10.11.6-MariaDB-0+deb12u1")), "mysql", "MariaDB"),
    (_postgres, "postgresql", "PostgreSQL"),
    (_neo4j, "neo4j", "Neo4j (Bolt 5.4)"),
    (lambda c: c.sendall(b"SSH-2.0-OpenSSH_9.6\r\n"), None, "SSH server"),
    (_http_neo4j, None, "Bolt port"),
    (_http_plain, None, "web server"),
])
def test_detects_what_answers(serve, handler, engine, words):
    found = detect("127.0.0.1", serve(handler), timeout=3)
    assert found.engine == engine and words in found.message, found


def test_mysql_that_refuses_this_host_is_still_mysql(serve):
    msg = b"\xff\x6a\x04Host '10.0.0.5' is not allowed to connect to this MySQL server"
    found = detect("127.0.0.1", serve(lambda c: c.sendall(len(msg).to_bytes(3, "little") + b"\x00" + msg)))
    assert found.engine == "mysql" and "not allowed" in found.message


def test_nothing_listening():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    found = detect("127.0.0.1", port, timeout=2)
    assert found.engine is None and "Could not reach" in found.message


def test_unknown_host():
    found = detect("no-such-host.invalid", 5432, timeout=2)
    assert found.engine is None and "could not be found" in found.message


@pytest.mark.parametrize("text, port, expected", [
    ("db.example.org", 5432, ("db.example.org", 5432)),
    ("db.example.org:5433", 0, ("db.example.org", 5433)),
    ("192.0.2.20:3306", 0, ("192.0.2.20", 3306)),
    ("bolt://graph.lab:7687", 0, ("graph.lab", 7687)),
    ("postgresql://reader@db.lab:6543/air", 0, ("db.lab", 6543)),
    ("[::1]:5432", 0, ("::1", 5432)),
    ("  db.lab  ", 7, ("db.lab", 7)),
])
def test_split_address(text, port, expected):
    assert split_address(text, port) == expected


def test_detect_button_fills_in_the_type(studio, serve):
    port = serve(_postgres)
    accounts.add_user("boss", "password1", "admin")
    at = _login(AppTest.from_file(APP), "boss")
    next(t for t in at.text_input if t.label.startswith("Host")).input(f"127.0.0.1:{port}")
    at.run(timeout=30)
    next(b for b in at.button if b.label == "Detect type").click()
    at.run(timeout=30)
    assert not at.exception, at.exception
    assert any("Found PostgreSQL" in s.value for s in at.success)
    assert next(s for s in at.selectbox if s.label == "Type").value == "postgresql"
    assert any(t.label == "User" for t in at.text_input)


def test_saving_takes_the_port_from_the_host_field(studio):
    connections.save(connections.Connection("x", "X", "postgresql", host="db.lab", port=6543, user="r"))
    assert connections.get("x").port == 6543
