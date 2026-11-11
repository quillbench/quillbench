# Changelog

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
