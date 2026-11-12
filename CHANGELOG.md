# Changelog

## 1.10.3 — 2026-11-12

- **Clearer form designer.** Each slot's title shows its label (as you type it), its kind and,
  for slots saved to the CSV, its column name. The column name can now be changed (lower-case
  letters, digits and _).
- **A one-time agreement per project.** Put the agreement as `agreement.md` in the project
  folder. Before anyone who is not an admin can use the project, they see its full text in a
  short scrolling box, a check box and an Enter button. Acceptance is written
  once to `agreement_acceptances.json` in the project folder (who, when, and a SHA-256
  fingerprint of the exact text), is never changed by the app, and the agreement is not
  shown anywhere in the app after that.

## 1.10.2 — 2026-11-12

- **HTTPS without a separate web server.** Set `ssl_cert` and `ssl_key` in `settings.yaml` to a
  certificate and its private key, and `quillbench serve` serves the site over HTTPS on its
  usual port. It stops with a clear message if either file is missing.

## 1.10.1 — 2026-11-12

- **The first admin can make or remove admins in the web page** (Admin → People → Admins).
  The first admin is the earliest admin account made on the server. Other admins cannot change
  roles, nobody can change their own role there, and university (LDAP) accounts still can never
  be admins. A changed person is signed out, so the new role applies from their next sign-in.
- On the Schema tab, *All tables at a glance* is now a collapsible box below the table picker.

## 1.10.0 — 2026-11-12

- **A fuller schema for the people writing queries**: columns, types, primary keys, links to
  other tables (foreign keys), descriptions stored in the database and an estimated row count.
  Tables split by date (`x_201904`, `x_201905`, …) and PostgreSQL partitions are shown once,
  with their parts listed; PostGIS bookkeeping tables are hidden.
- **Schema on its own tab.** Benchmark projects get a **Schema** tab: a dropdown to pick one
  table and see its columns, keys, row count, the admin's note and its first 10 rows; below it,
  a collapsible one-line-per-table overview with a copy button (handy for an AI assistant).
- **Students see their scores, not the scoring rules.** Categories, points per category and
  the "how points work" table are for admins only; students see complexity, points, targets
  left and what is still needed.
- **Verdicts are optional and off by default.** Admins switch them on per project.
- **Pop-up notifications** for the result of *Add to benchmark* and *Check similarity*.
- **Admins choose what the schema shows**: which tables people see, a note per table
  (meanings, units, codes), and whether the first 10 rows are shown.

## 1.9.0 — 2026-11-12

- **Queries are sorted into categories automatically.** When someone runs a query, QuillBench
  reads it and shows the categories it falls into: simple lookup, filter, single and multi
  join, outer join, COUNT, SUM, AVG, MIN, MAX, GROUP BY, HAVING, sorting, top-N, DISTINCT,
  subquery, WITH, UNION, window functions, CASE, dates and times, text matching, NULL checks
  and ranges; for Neo4j also one relationship, multi hop, variable-length and shortest paths.
  Words inside strings and comments are ignored. The category ids are saved in a new
  `categories` column.
- **Points and targets.** Admins set the points each category is worth, the minimum points
  each person should reach per category, and an overall minimum (form designer → Categories
  and points). An entry earns the points of its most valuable category only. Totals are
  recalculated from the saved queries, so changing the points later is fair to everyone.
- **Complexity level for every query.** After a query runs, and on the Profile tab, each entry
  shows its complexity, Easy, Medium, Hard or Very hard, set by the system from its hardest
  category (it does not change when admins change the points).
- **Profile tab.** Each person sees their points against the goal, points and entries per
  category with progress bars, what is still needed, and all their entries. Admins can open
  anyone's profile. The Progress page adds points and open targets per person and a chart of
  entries per category.
- **Verdict buttons instead of the tick box.** Under the result, people choose Correct,
  Incorrect or Uncertain; admins can rename or add buttons and choose which verdicts may be
  added and which earn points (by default all may be added, only Correct earns points). The
  choice is saved in a new `verdict` column. Entries made before this version count as Correct.
- **Check similarity before adding.** A *Check similarity* button under the question says how
  close it is to the nearest question in the benchmark and whether it would be added (a
  classmate's question is never shown). Admins get a *Similarity checker* on the Progress tab:
  the 10 closest questions to any text, and the added pairs that came closest to the limit.
  Added entries now also keep `similarity` and `matched_id`.
- **Only admins can download data.** The hover toolbar on tables (with *Download as CSV*) is
  hidden for everyone else.

## 1.8.1 — 2026-11-11

- **Connect to a database by host and port.** Under Admin → Connections, enter where the
  database is (`db.example.org`, `db.example.org:5432`, or a `postgresql://` / `bolt://`
  address) and click **Detect type**: QuillBench asks the port what is listening, without
  logging in, and fills in PostgreSQL, MySQL/MariaDB or Neo4j. It says so when the port is
  something else (a web page, SSH, Neo4j's web port instead of its Bolt port) or cannot be
  reached.
- A new install with no projects now shows New project, Connections and People together, so
  the database can be added before the first benchmark project is created.

## 1.8.0 — 2026-11-11

- **Build a benchmark with people.** A new project starts by asking what it is for: annotating
  items you already have, or building a new benchmark. In a benchmark project, people read the
  database schema and worked examples, write a question and its query, run it, check the
  result and add the entry. *Add* unlocks only when the query ran without error, returned at
  least the set number of rows, has not changed since it ran, and the result box is ticked.
- **A form you design.** Admins add, label, reorder and remove slots: text blocks, the live
  database schema, worked examples, question, query editor, result, label buttons, dropdown,
  tick boxes, slider, short answer and paragraph. Extra slots become columns in the CSV.
- **Duplicate questions are kept out.** A question 80% or more similar (the limit is
  adjustable) to any accepted entry, from anyone, is not added: the person is told, and the
  entry goes to `rejected.csv` with its id, the id it matched and the score. Accepted entries
  go to `benchmark.csv`. Every entry gets a random id that never repeats.
- **Database connections** (Admin → Connections) for PostgreSQL, MySQL/MariaDB, SQLite and
  Neo4j. Login details stay in the studio's private folder; people see only the name. Queries
  run read-only, with a time limit. PostgreSQL and MySQL need
  `pip install 'quillbench[postgres]'` or `'quillbench[mysql]'`.
- **Admins are made on the server only**, with `quillbench user add ID --admin` or
  `quillbench user role ID admin`. The web pages no longer grant or remove the admin role, and
  the first visitor no longer becomes admin. University (LDAP) accounts are never admins.

## 1.7.0 — 2026-11-11

- One version for every install. Servers that ran a private 1.5–1.6 copy and the PyPI
  release are now the same code; this number is higher than both.
- Workspaces whose manifest still has the older name `pas_workspace.yaml` are found
  automatically, like `labelwise_workspace.yaml` already was.

## 1.4.2 — 2026-11-03

- Step-by-step guide for installing on an Ubuntu server: `docs/install-ubuntu.md`.
- Admins can add people who sign in with their university (LDAP) login: Admin → People, or
  `quillbench user add ID --ldap`.

## 1.4.1 — 2026-11-03

- First release on PyPI: `pip install quillbench`.

## 1.4.0 — 2026-11-03

- New sign-in page: the logo draws itself in, with a short welcome and a clearer form. It works
  on phones and respects the system's reduce-motion setting.
- The top bar shows the logo and the full name.

## 1.3.0 — 2026-11-03

- Optional sign-in with LDAP or Active Directory accounts (`pip install "quillbench[ldap]"`
  and an `ldap` block in settings.yaml). Local accounts work as before.
- `quillbench connect ADDRESS` and `quillbench open`: annotators install QuillBench, connect
  to their group's server once, and open it with one command.
- Fixed: the `quillbench` command was missing from the package, so `quillbench serve` and the
  other commands did not work after installing from GitHub.

## 1.2.0 — 2026-11-03

- Renamed to QuillBench: the package, the `quillbench` command, the `QUILLBENCH_HOME` setting
  and the new `quillbench_workspace.yaml` manifests.
- Earlier installs keep working: `labelwise_workspace.yaml`, `labelwise_assignments.json`,
  `~/.config/labelwise` and `LABELWISE_HOME` are still read.
- New quill logo.

## 1.1.1 — 2026-11-02

- Admin → Projects: create a project without a CSV, upload CSVs at any time, split them into
  batches, and assign batches later from any screen selection.
- The page stays on the same tab after an upload.

## 1.1.0 — 2026-11-02

The first account becomes the admin. Admins can create projects, upload CSVs, set labels, split
batches and assign them from the interface.

## 1.0.0 — 2026-11-02

First public release.
