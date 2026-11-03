"""LDAP sign-in and the annotators' connect command."""

import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from quillbench import accounts, client, ldap_auth

LDAP = {"url": "ldap://ldap.example.edu", "user_dn": "uid={user},ou=people,dc=example,dc=edu"}


@pytest.fixture
def fake_ldap(monkeypatch):
    """An LDAP server that knows one person, stud1 / studpass1."""
    seen = []

    def check(name, password, conf):
        seen.append(name)
        return name == "stud1" and password == "studpass1"
    monkeypatch.setattr(ldap_auth, "check", check)
    return seen


def test_ldap_first_sign_in_makes_an_annotator(studio, fake_ldap):
    assert accounts.sign_in("stud1", "studpass1", accounts.load_users(), LDAP)
    record = accounts.load_users()["stud1"]
    assert record["role"] == "annotator" and record["source"] == "ldap"
    assert "password_hash" not in record


def test_ldap_wrong_password_makes_nothing(studio, fake_ldap):
    assert not accounts.sign_in("stud1", "wrong-pass", accounts.load_users(), LDAP)
    assert accounts.load_users() == {}


def test_local_password_wins_over_ldap(studio, fake_ldap):
    accounts.add_user("stud1", "localpass1")
    users = accounts.load_users()
    assert not accounts.sign_in("stud1", "studpass1", users, LDAP)
    assert accounts.sign_in("stud1", "localpass1", users, LDAP)
    assert fake_ldap == []


def test_disabled_ldap_account_cannot_sign_in(studio, fake_ldap):
    accounts.sign_in("stud1", "studpass1", accounts.load_users(), LDAP)
    accounts.update_user("stud1", active=False)
    assert not accounts.sign_in("stud1", "studpass1", accounts.load_users(), LDAP)


def test_without_auto_create_only_added_people_get_in(studio, fake_ldap):
    conf = {**LDAP, "auto_create": False}
    assert not accounts.sign_in("stud1", "studpass1", accounts.load_users(), conf)
    users = accounts.load_users()
    users["stud1"] = {"role": "annotator", "active": True, "source": "ldap"}
    accounts.save_users(users)
    assert accounts.sign_in("stud1", "studpass1", accounts.load_users(), conf)


def test_no_ldap_means_local_accounts_only(studio, fake_ldap):
    assert not accounts.sign_in("stud1", "studpass1", {}, {})
    assert fake_ldap == []


@pytest.mark.parametrize("name,password", [("stud1", ""), ("a,b", "x"), ("*", "x"), ("x)(uid=*", "x")])
def test_ldap_rejects_empty_passwords_and_odd_ids(name, password):
    assert not ldap_auth.check(name, password, LDAP)


@pytest.mark.skipif(not os.environ.get("QB_TEST_LDAP_URL"), reason="needs a test LDAP server")
def test_real_ldap_server():
    conf = {"url": os.environ["QB_TEST_LDAP_URL"], "user_dn": "uid={user},ou=people,dc=example,dc=edu"}
    assert ldap_auth.check("stud1", "studpass1", conf)
    assert not ldap_auth.check("stud1", "wrong", conf)
    assert not ldap_auth.check("nobody", "studpass1", conf)


class _Health(BaseHTTPRequestHandler):
    def do_GET(self):
        ok = self.path == "/qb/_stcore/health"
        self.send_response(200 if ok else 404)
        self.end_headers()
        self.wfile.write(b"ok" if ok else b"no")

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Health)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def test_connect_finds_and_remembers_the_server(server, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    url = client.find_server(server + "/qb/")
    assert url == f"http://{server}/qb/"
    client.save(url)
    assert client.saved() == url


def test_connect_reports_a_wrong_address(server):
    with pytest.raises(ValueError, match="No QuillBench server"):
        client.find_server(server + "/wrong")
    with pytest.raises(ValueError, match="https:// or http://"):
        client.find_server("ftp://example.edu")


def test_command_line_has_its_commands(capsys):
    from quillbench import cli
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert all(c in out for c in ("serve", "user", "connect", "open"))
