<p align="center"><img src="docs/logo.svg" width="120" alt="QuillBench logo"></p>

# QuillBench

Build benchmark datasets with human annotators. A config-driven annotation platform for research, by **A.C. Pogaku**.

The software lives in this folder. Each paper keeps its own annotation data in its own
folder, a **workspace**, described by one `quillbench_workspace.yaml` file. The studio reads
that file and writes answers straight into the paper's folders, so there is nothing to
copy and the two are always in step.

```
~/quillbench/          the software (this repo)
~/.config/quillbench/  private studio state: registry, accounts (hashed), settings
<paper>/quillbench_workspace.yaml           one paper's projects
<paper>/batches/*.csv|*.jsonl        items to annotate, one file per batch
<paper>/progress/<user>__<batch>.csv answers, written by the studio
<paper>/quillbench_assignments.json         who works on which batch (no secrets)
<paper>/quillbench_exports/<time>/          exports: answers.csv/.jsonl, agreement.csv, DATASHEET.md
```

## Getting started in the browser

Run `quillbench serve` and open the page. The first person to sign up becomes the admin, so do
this yourself before sharing the address. Then, with no files to edit, the admin can use
**Admin → Projects** to create a project, upload CSVs at any time, set label buttons or a slider, split
the items into batches and assign them. Add people under **Admin → People**, and follow the
work in the **Progress** tab.

## Task types

| task | what the annotator does |
|---|---|
| `rating` | moves a slider on a scale (e.g. 0–1 or 1–5), optional "can't judge" |
| `choice` | picks one of the labels the manifest lists |
| `text` | writes a short answer |
| `verdict`, `gate_review`, `gate_calibration`, `item_question`, `item_query`, `item_disposition` | reviews a question + reference query + precomputed results |
| `adjudication` | judges a predicted query against a reference (results withheld) |
| `pair_semantics` | blinded A/B comparison of two queries |

Per project the manifest can set `instructions`, `note_label`, `note_placeholder`,
`note_required_for`, `display` (which item columns to show and how), `checks`
(hidden attention-check items with expected answers) and `also_accept` (labels
from an earlier version of the task that still count as answers).

## Everyday commands

```bash
quillbench init-workspace ~/my_paper/annotation      # start a new paper from a template
quillbench workspace add ~/my_paper/annotation        # register it
quillbench validate                                   # check every workspace
quillbench user add alice                             # asks for a password
quillbench assign alice my_paper rate_01 rate_02
quillbench status my_paper                            # progress per person and batch
quillbench quality my_paper                           # Krippendorff's alpha, Cohen's kappa, checks, time
quillbench export my_paper                            # anonymised answers + datasheet
quillbench backup                                     # also runs nightly
quillbench serve                                      # http://127.0.0.1:8560/quillbench/
```

(`python -m quillbench …` works the same without installing; `pip install -e .` adds the `quillbench` command.)

In the app: **1–9** answer, **S** skip, **J/→** next, **K/←** previous, **Enter** saves a rating.

Useful manifest options for rating studies:

```yaml
scale: {min: 0, max: 1, step: 0.05, unsure_label: "Can't judge",
        marks: {0: false, 0.5: half true, 1: true}}   # words along the slider
display:
  - {field: statement, label: Statement}             # the first text field is shown large
  - {field: hourly, as: chart, chart: {kind: bar, x_label: hour, lines: [15.5, 35.5]}}
practice: true      # show each item's 'expected' and 'explanation' columns after answering
```

## Running as a service

```bash
cp deploy/quillbench.service deploy/quillbench-backup.service deploy/quillbench-backup.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now quillbench quillbench-backup.timer
sudo loginctl enable-linger $USER     # keep it running after logout
```

nginx: add `deploy/nginx-quillbench.conf` to the server block.

## Quality measures

* **Krippendorff's alpha** (nominal for labels; interval or ordinal for ratings) over all
  items answered by two or more people; skips and "can't judge" left out. Verified against
  the worked example in Krippendorff (2011): 0.743 / 0.815 / 0.849.
* **Cohen's kappa** per pair of annotators (labels), or mean absolute difference and
  Pearson r (ratings).
* **Check items** passed per annotator, and **median seconds per item** (QuillBench records
  `updated_at` and `seconds` for every answer).

## Security

Passwords are stored as salted PBKDF2-SHA256 hashes in the studio home (mode 600).
Sessions use an HMAC-signed cookie. Exports replace annotator ids with codes
(A01, …); the code book stays in the studio home.

## Origin

© 2026 A.C. Pogaku. Free for academic, non-commercial use; commercial use is not permitted. See [LICENSE](LICENSE).
