# Changelog

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
