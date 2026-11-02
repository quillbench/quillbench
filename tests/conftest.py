import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """A private studio home for the test."""
    home = tmp_path / "studio"
    monkeypatch.setenv("LABELWISE_HOME", str(home))
    return home


MANIFEST = """\
workspace:
  id: demo
  name: Demo paper
  batches_dir: batches
  progress_dir: progress
projects:
  - id: demo_rating
    name: Rating study
    task: rating
    batch_prefix: rate_
    scale: {min: 0, max: 1, step: 0.05, unsure_label: "Can't judge"}
    display: [question, {field: context, as: markdown, collapsed: true}]
    checks: {c1: [0.9, 1.0]}
    instructions: Rate how well the label fits.
  - id: demo_choice
    name: Choice study
    task: choice
    batch_prefix: pick_
    labels: [{value: "YES", text: "Yes", primary: true}, "NO", {value: MAYBE, text: Maybe}]
    note_required_for: [MAYBE]
  - id: demo_verdict
    name: Query review
    task: verdict
    default: true
    benchmark_csv: bench.csv
    gold_cache: {file: gold.json}
    also_accept: [ACCEPT]
"""


@pytest.fixture
def workspace(tmp_path, studio):
    root = tmp_path / "paper"
    (root / "batches").mkdir(parents=True)
    (root / "labelwise_workspace.yaml").write_text(MANIFEST)
    (root / "batches" / "rate_01.csv").write_text(
        "id,question,context\nq1,Is it hot?,**36 °C**\nq2,Is it cold?,2 °C\nc1,Check item,x\n")
    with (root / "batches" / "pick_01.jsonl").open("w") as f:
        for i in range(3):
            f.write(json.dumps({"id": f"p{i}", "text": f"item {i}"}) + "\n")
    (root / "bench.csv").write_text("id,nl_query,gold_cypher\n1,how many?,MATCH (n) RETURN count(n)\n")
    (root / "gold.json").write_text(json.dumps({"1": {"records": [{"c": 3}], "elapsed": 0.1}}))
    (root / "batches" / "b_01.csv").write_text("id,nl_query,gold_cypher\n1,how many?,MATCH (n) RETURN count(n)\n")
    return root
