# Installing QuillBench on an Ubuntu server

This guide sets up QuillBench on an Ubuntu server (22.04 or newer) so a group can annotate
from their own computers. It covers installing, signing in with university (LDAP) accounts,
keeping the service running, three ways to let people reach it, and backups.

Run every command as the ordinary user account that will own QuillBench, not as root. Commands
that need `sudo` say so. Replace values written in CAPITALS, such as `YOURNAME`, with your own.

**Contents**

1. [Install](#1-install)
2. [Settings and the first admin](#2-settings-and-the-first-admin)
3. [Sign in with university accounts (LDAP), optional](#3-sign-in-with-university-accounts-ldap-optional)
4. [Run it as a service](#4-run-it-as-a-service)
5. [Let people reach it](#5-let-people-reach-it)
6. [How annotators connect](#6-how-annotators-connect)
7. [Nightly backups](#7-nightly-backups)
8. [Updating](#8-updating)
9. [Troubleshooting](#9-troubleshooting)

---

## 1. Install

QuillBench needs Python 3.10 or newer. Ubuntu 22.04 and later already have it.

```bash
python3 --version
```

Install the tools for a private Python environment:

```bash
sudo apt update && sudo apt install -y python3-venv
```

Create the environment and install QuillBench. The `[ldap]` part adds university sign-in; leave
it out if you will only use QuillBench's own accounts.

```bash
python3 -m venv ~/quillbench-env
```

```bash
~/quillbench-env/bin/pip install "quillbench[ldap]"
```

```bash
~/quillbench-env/bin/quillbench version
```

The last command prints the version and the **studio home**, `~/.config/quillbench`, where
QuillBench keeps its settings and accounts. Each project's data is kept in its own folder.

---

## 2. Settings and the first admin

Create the settings file:

```bash
mkdir -p ~/.config/quillbench && nano ~/.config/quillbench/settings.yaml
```

```yaml
title: QuillBench
subtitle: Human annotation for research datasets
```

Every setting is optional. The ones you are most likely to change:

| Setting | Default | What it does |
|---|---|---|
| `title`, `subtitle` | QuillBench | Shown on the sign-in page and the top bar |
| `port` | `8560` | Port QuillBench listens on |
| `base_url_path` | `quillbench` | The path in the address: `http://server:8560/quillbench/` |
| `backup_dir` | `~/quillbench_backups` | Where backups are written |
| `session_max_age_days` | `30` | How long a sign-in lasts |
| `ldap` | none | University sign-in, see section 3 |

Create your admin account on the server. Admins can only be made this way: the web pages never
grant the role, and university (LDAP) accounts can never be admins.

```bash
~/quillbench-env/bin/quillbench user add YOURNAME --admin
```

It asks for a password twice (8 characters or more). This account has its own QuillBench
password, separate from any university login.

---

## 3. Sign in with university accounts (LDAP), optional

With LDAP turned on, people sign in with the username and password they already use at the
university. QuillBench asks the LDAP server to check the password and never stores it.

### 3.1 Find your LDAP details

You need two things: the server address, and how a username becomes an LDAP entry (the
*user DN*). If the server already uses LDAP logins through SSSD, the details are in its
configuration:

```bash
sudo grep -h -E "^\s*(ldap_uri|ldap_search_base|ldap_user_search_base|ad_domain)" /etc/sssd/sssd.conf
```

```bash
grep -h -E "^\s*(URI|BASE)" /etc/ldap/ldap.conf 2>/dev/null
```

Typical results and what they mean:

| You see | Use in QuillBench |
|---|---|
| `ldap_uri = ldaps://ldap.example.ac.jp` | `url: ldaps://ldap.example.ac.jp:636` |
| `ldap_user_search_base = ou=People,dc=example,dc=ac,dc=jp` | `user_dn: "uid={user},ou=People,dc=example,dc=ac,dc=jp"` |
| `ad_domain = example.ac.jp` (Active Directory) | `user_dn: "{user}@example.ac.jp"` |

If none of these files exist, ask the server's administrators for the LDAP address and the
"bind DN" pattern for users.

### 3.2 Test the details with your own login

Install the LDAP command-line tools:

```bash
sudo apt install -y ldap-utils
```

Try signing in as yourself (it asks for your university password):

```bash
ldapwhoami -x -H ldaps://ldap.example.ac.jp:636 -D "uid=YOURID,ou=People,dc=example,dc=ac,dc=jp" -W
```

* It prints `dn:uid=YOURID,...`: the details are right. Continue to 3.3.
* `Invalid credentials (49)` with the correct password: the entries are named differently.
  Find the real pattern with the search below and copy its `dn:` line, putting `{user}` where
  your ID appears:

  ```bash
  ldapsearch -x -H ldaps://ldap.example.ac.jp:636 -b "ou=People,dc=example,dc=ac,dc=jp" "(uid=YOURID)" dn
  ```

* A certificate error: see [Troubleshooting](#9-troubleshooting).

### 3.3 Turn it on

Add an `ldap` block to `~/.config/quillbench/settings.yaml`:

```yaml
ldap:
  url: ldaps://ldap.example.ac.jp:636
  user_dn: "uid={user},ou=People,dc=example,dc=ac,dc=jp"
  auto_create: true
```

| Key | Meaning |
|---|---|
| `url` | The LDAP server. Use `ldaps://` (encrypted). For a plain `ldap://` server, add `start_tls: true` |
| `user_dn` | The entry for a user, with `{user}` where their username goes |
| `auto_create` | `true`: anyone the LDAP server accepts can sign in, and gets an annotator account with no work assigned. `false`: only people an admin has added can sign in |
| `timeout` | Seconds to wait for the LDAP server (default 10) |

How sign-in works:

* An account with its own QuillBench password (such as the admin from section 2) always uses
  that password. LDAP can never take over such an account.
* A new LDAP user sees nothing until an admin assigns them batches.
* Admins can disable anyone, including LDAP users, under **Admin → People**.

With `auto_create: false`, add each person before they sign in. In **Admin → People → Add a
person**, enter their university ID and keep **Signs in with their university login** ticked
(leave the password empty). Or from the command line:

```bash
~/quillbench-env/bin/quillbench user add STUDENTID --ldap
```

---

## 4. Run it as a service

A systemd user service starts QuillBench when the server boots and restarts it if it stops.

```bash
mkdir -p ~/.config/systemd/user && nano ~/.config/systemd/user/quillbench.service
```

```ini
[Unit]
Description=QuillBench
After=network.target

[Service]
ExecStart=%h/quillbench-env/bin/quillbench serve
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
```

Start it, and let it keep running after you log out:

```bash
systemctl --user daemon-reload && systemctl --user enable --now quillbench
```

```bash
sudo loginctl enable-linger $USER
```

Check it is running. This should print `ok`:

```bash
curl -s http://127.0.0.1:8560/quillbench/_stcore/health
```

At this point QuillBench only answers on the server itself. Choose how people reach it in the
next section.

---

## 5. Let people reach it

| | Option | Best for | Encrypted | Needs sudo |
|---|---|---|---|---|
| A | SSH tunnel | You and people who already have SSH access | Yes | No |
| B | `http://IP:PORT/quillbench/` | Quick tests, or a trusted closed network | **No** | For the firewall |
| C | nginx with https | Real use with many people | Yes | Yes |

### Option A: SSH tunnel

Nothing changes on the server. Each person runs this on **their own computer** and leaves it
open while they work:

```bash
ssh -N -L 8560:127.0.0.1:8560 USERNAME@SERVER
```

Then they open **http://localhost:8560/quillbench/**. SSH encrypts the connection, so plain
`http` is safe here. This only works for people who can SSH into the server.

### Option B: direct address, `http://IP:PORT/quillbench/`

1. Make QuillBench listen on the network. In `~/.config/systemd/user/quillbench.service`,
   change the start line to:

   ```ini
   ExecStart=%h/quillbench-env/bin/quillbench serve --address 0.0.0.0 --port 8560
   ```

2. To change the path after the port, set `base_url_path` in `settings.yaml`, for example
   `base_url_path: annotate` for `http://IP:8560/annotate/`. Leave it out to keep `/quillbench/`.

3. Restart, and check it is listening on `0.0.0.0:8560`:

   ```bash
   systemctl --user daemon-reload && systemctl --user restart quillbench
   ```

   ```bash
   ss -ltn | grep 8560
   ```

4. Open the port in Ubuntu's firewall:

   ```bash
   sudo ufw allow 8560/tcp
   ```

5. From another computer, open `http://SERVER-IP:8560/quillbench/`.

If the page opens on the server but not from other computers, the university's network
firewall is probably blocking the port. Try from inside the campus network or VPN, or ask the
network administrators to open it.

> **Warning.** This address is not encrypted. Passwords, including university passwords when
> LDAP is on, cross the network as plain text. Use it for testing or on a network you trust,
> and use option A or C for real work.

### Option C: nginx with https

People open `https://SERVER-NAME/quillbench/`, and passwords are encrypted. QuillBench itself
stays reachable only from the server, so keep the service as in section 4 (without
`--address 0.0.0.0`). If you tried option B, close its port again with
`sudo ufw delete allow 8560/tcp`.

1. Install nginx:

   ```bash
   sudo apt install -y nginx
   ```

2. Get a certificate. University servers usually have one from IT. For a server with a public
   name, Let's Encrypt provides one for free:

   ```bash
   sudo apt install -y certbot python3-certbot-nginx
   ```

   ```bash
   sudo certbot --nginx -d SERVER-NAME
   ```

3. Add this inside the `server { listen 443 ssl; ... }` block of your site's file in
   `/etc/nginx/sites-enabled/` (for Let's Encrypt, the file certbot edited):

   ```nginx
   location /quillbench/ {
       proxy_pass http://127.0.0.1:8560/quillbench/;
       proxy_http_version 1.1;
       proxy_set_header Upgrade $http_upgrade;
       proxy_set_header Connection "upgrade";
       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
       proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
       proxy_set_header X-Forwarded-Proto $scheme;
       proxy_read_timeout 86400;
       client_max_body_size 50m;
   }
   ```

   The `Upgrade` lines are needed: the page keeps a live connection open to the server.
   If you changed `base_url_path`, use that path in both places instead of `quillbench`.

4. Check the configuration, reload nginx, and allow https through the firewall:

   ```bash
   sudo nginx -t && sudo systemctl reload nginx
   ```

   ```bash
   sudo ufw allow 443/tcp
   ```

5. Open `https://SERVER-NAME/quillbench/`.

---

## 6. How annotators connect

Annotators can simply open the address in a browser. Or they can install QuillBench on their
own computer, which remembers the address for them:

```bash
pip install quillbench
```

```bash
quillbench connect SERVER-NAME/quillbench
```

Use the address that matches your option: `localhost:8560/quillbench` with an SSH tunnel (A),
`SERVER-IP:8560/quillbench` (B), or `SERVER-NAME/quillbench` (C). After that,
`quillbench open` opens the sign-in page. Answers are saved on the server, not on their
computer.

Give people work in the web interface: **Admin → Projects** to create a project, upload a CSV,
split it into batches and assign them; **Admin → People** to add or disable accounts;
**Progress** to follow the work.

---

## 7. Nightly backups

A backup is one `.tar.gz` of all answers, assignments, accounts and settings, and of projects
created in the web interface, written to
`backup_dir` (default `~/quillbench_backups`). The newest 14 are kept. Batch files of
workspaces set up from the command line are not included; keep those in the paper's own
folder or version control. Make one now:

```bash
~/quillbench-env/bin/quillbench backup
```

To make one every night at 02:30, create two files.

```bash
nano ~/.config/systemd/user/quillbench-backup.service
```

```ini
[Unit]
Description=QuillBench backup

[Service]
Type=oneshot
ExecStart=%h/quillbench-env/bin/quillbench backup
```

```bash
nano ~/.config/systemd/user/quillbench-backup.timer
```

```ini
[Unit]
Description=Back up QuillBench every night at 02:30

[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload && systemctl --user enable --now quillbench-backup.timer
```

```bash
systemctl --user list-timers | grep quillbench
```

Keep a copy somewhere other than this server too, for example by setting `backup_dir` to a
mounted network drive.

---

## 8. Updating

```bash
~/quillbench-env/bin/pip install -U "quillbench[ldap]"
```

```bash
systemctl --user restart quillbench
```

The restart takes a few seconds. Annotators see a short reconnect and stay signed in; saved
answers are not affected.

---

## 9. Troubleshooting

**See what QuillBench is doing.** Most problems show up in the log:

```bash
journalctl --user -u quillbench -n 50 --no-pager
```

| Problem | What to check |
|---|---|
| `curl` health check prints nothing | Is the service running? `systemctl --user status quillbench` |
| Works on the server, not from other computers | Option B: `--address 0.0.0.0` set, `sudo ufw status` shows the port, campus firewall. Option C: `sudo nginx -t`, port 443 open |
| Page loads but stays blank or keeps reconnecting behind nginx | The `Upgrade` and `Connection` lines are missing from the nginx block |
| `Not Found` at the address | The path does not match `base_url_path`. Default is `/quillbench/`, with the trailing slash |
| Service stops when you log out | Run `sudo loginctl enable-linger $USER` |
| "Wrong user ID or password" for every LDAP user | Repeat the `ldapwhoami` test in 3.2 with the same values as `settings.yaml`. The log names the LDAP error |
| Log says `ldap3 is not installed` | Install with the extra: `~/quillbench-env/bin/pip install "quillbench[ldap]"`, then restart |
| `ldapwhoami` reports a certificate error | The LDAP server uses a university certificate your system does not trust. Ask IT for the CA certificate (SSSD's `ldap_tls_cacert` setting names it), copy it to `/usr/local/share/ca-certificates/` with a `.crt` ending, and run `sudo update-ca-certificates` |
| An LDAP user cannot sign in with `auto_create: false` | Add them first as a university user (section 3.3) |
| Someone's university login stopped working after they were given a password in Admin → People | Accounts with their own QuillBench password use it instead of LDAP |

Check that your settings file is valid YAML (it prints the settings, or an error):

```bash
~/quillbench-env/bin/python -c "from quillbench.settings import load_settings; print(load_settings())"
```
