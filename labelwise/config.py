"""
Workspace configuration: one paper's annotation projects, read from its manifest.

A *workspace* is a folder that belongs to a paper. Its manifest,
``labelwise_workspace.yaml``, declares the paper's annotation projects; every path in it
is resolved relative to the manifest's folder (absolute paths and ``~`` also work,
so a workspace can point at data that already lives elsewhere). The studio never
moves or copies workspace data -- it reads and writes it in place.

Inside a workspace the original routing rule is kept: a batch file's name prefix
selects its project (longest prefix wins; one project may be the default).

Validation policy
----------------------------------------------------------
* **Configured inputs** (benchmark CSV, gold cache, shard dir) missing or malformed
  -> hard failure with a specific message, never a silently blank screen.
* **Runtime state** (a batch with no progress file yet) missing -> empty frame is
  correct and expected.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .tasks import BUILTIN, TASKS, TaskSpec, customise

MANIFEST_NAME = "labelwise_workspace.yaml"

# Columns every query-review benchmark CSV must provide. Everything else (category,
# difficulty, split, local_id, ...) is optional and rendered only when present.
REQUIRED_BENCHMARK_COLUMNS = ("id", "nl_query", "gold_cypher")
REQUIRED_BATCH_COLUMNS = ("id",)

_IDENT_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class ConfigError(Exception):
    """Raised for any malformed or unusable configuration.

    Carries a message written for a human operator: what is wrong, which config
    key it came from, and which path was checked.
    """


# ──────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GraphDBConfig:
    """Connection details for a project's target graph database.

    The password is never stored in the config file. The config declares the
    *name of an environment variable* (``password_env``) to read it from,
    matching the ``os.environ.get("NEO4J_PASSWORD", "")`` pattern the original
    tool used.
    """

    label: str
    uri: str
    user: str
    password_env: str
    database: str | None = None

    @property
    def password(self) -> str:
        return os.environ.get(self.password_env, "")

    @property
    def password_available(self) -> bool:
        return bool(os.environ.get(self.password_env, ""))


@dataclass(frozen=True)
class JuryConfig:
    """Optional per-project LLM-jury result display.

    Generalizes the original's hardcoded SGCJ panel (fixed file, fixed
    ``jury_verdict`` column, fixed ``["qwen3","llama3","gemma3"]`` judge list)
    into: a CSV, an id column to join on, an aggregate-verdict column, and a
    list of judge names whose per-judge verdict/reason columns are derived
    from two format templates. That is exactly as much abstraction as the
    real display needed — no more.
    """

    csv_path: Path
    id_column: str = "id"
    verdict_column: str = "jury_verdict"
    judges: tuple[str, ...] = ()
    judge_verdict_template: str = "{judge}_verdict"
    judge_reason_template: str = "{judge}_reason"
    title: str = "LLM Jury Assessment"
    # When true the panel is suppressed even though it is configured — the
    # original hid jury output during "fair annotation" rounds so annotators
    # were not anchored by the model's opinion.
    hidden: bool = False
    hidden_message: str = (
        "LLM jury results are hidden for this batch during blind annotation."
    )

    def verdict_col_for(self, judge: str) -> str:
        return self.judge_verdict_template.format(judge=judge)

    def reason_col_for(self, judge: str) -> str:
        return self.judge_reason_template.format(judge=judge)


@dataclass(frozen=True)
class SchemaHelpSection:
    """One column of the free-text schema reference in the Query Console."""

    title: str
    body: str



@dataclass(frozen=True)
class DisplayField:
    """One item field shown on the annotation screen of a generic task."""

    column: str
    label: str = ""
    as_: str = "text"       # text, markdown, code, json, table, chart
    language: str = ""      # for code
    collapsed: bool = False
    chart: dict = field(default_factory=dict)   # for chart: kind, x_label, y_label, lines


@dataclass(frozen=True)
class ProjectConfig:
    """One annotation project: its items, its task, where its answers are written."""

    id: str
    name: str
    batch_prefix: str
    task_spec: TaskSpec
    benchmark_csv: Path | None = None
    batches_dir: Path | None = None
    progress_dir: Path | None = None
    gold_cache_file: Path | None = None
    gold_cache_shard_dir: Path | None = None
    gold_cache_shard_template: str = "{batch}_cache.json"
    graph_db: GraphDBConfig | None = None
    jury: JuryConfig | None = None
    schema_help: tuple[SchemaHelpSection, ...] = ()
    is_default: bool = False
    cypher_placeholder: str = ""
    instructions: str = ""
    archived: bool = False
    ask_paraphrase: bool = True
    display: tuple[DisplayField, ...] = ()
    checks: dict = field(default_factory=dict)   # item id -> expected answer (quality checks)
    description: str = ""
    # Practice round: after each answer show the item's 'expected' and 'explanation'
    # columns instead of moving on, so annotators calibrate before the real items.
    practice: bool = False

    @property
    def task(self) -> str:
        return self.task_spec.name

    @property
    def verdicts(self) -> tuple[str, ...]:
        return self.task_spec.values

    @property
    def sharded(self) -> bool:
        return self.gold_cache_shard_dir is not None

    def shard_path(self, batch_name: str) -> Path:
        if self.gold_cache_shard_dir is None:
            raise ConfigError(
                f"project '{self.id}': shard_path() called but no "
                f"gold_cache.shard_dir is configured."
            )
        return self.gold_cache_shard_dir / self.gold_cache_shard_template.format(
            batch=batch_name
        )


@dataclass(frozen=True)
class WorkspaceConfig:
    """One paper's annotation workspace."""

    id: str
    name: str
    root: Path
    manifest: Path
    batches_dir: Path
    progress_dir: Path
    assignments_file: Path
    owner: str = ""
    description: str = ""
    archived: bool = False
    projects: tuple[ProjectConfig, ...] = field(default_factory=tuple)
    # studio-wide display settings, copied in at load time
    display_row_cap: int = 300
    query_timeout_seconds: int = 30

    # ── batch -> project routing ──────────────────────────────────────────
    def project_for_batch(self, batch_name: str) -> ProjectConfig:
        """Longest-matching declared prefix wins; otherwise the default project.

        Longest-match (rather than first-match) means ``foo_`` and ``foo_pilot_``
        can coexist without ordering games in the YAML.
        """
        name = str(batch_name)
        best: ProjectConfig | None = None
        for proj in self.projects:
            if proj.batch_prefix and name.startswith(proj.batch_prefix):
                if best is None or len(proj.batch_prefix) > len(best.batch_prefix):
                    best = proj
        if best is not None:
            return best
        for proj in self.projects:
            if proj.is_default:
                return proj
        raise ConfigError(
            f"workspace '{self.id}': batch '{batch_name}' matches no project's "
            f"batch_prefix and no project is marked 'default: true'. Rename the batch "
            f"file to start with one of "
            f"{[p.batch_prefix for p in self.projects if p.batch_prefix]!r} "
            f"or mark one project as the default."
        )

    def project_by_id(self, project_id: str) -> ProjectConfig:
        for proj in self.projects:
            if proj.id == project_id:
                return proj
        raise ConfigError(f"workspace '{self.id}': no project with id '{project_id}'.")

    def batches_dir_for(self, project: ProjectConfig) -> Path:
        return project.batches_dir if project.batches_dir is not None else self.batches_dir

    def progress_dir_for(self, project: ProjectConfig) -> Path:
        return project.progress_dir if project.progress_dir is not None else self.progress_dir

    @property
    def all_batches_dirs(self) -> list[Path]:
        seen: list[Path] = []
        for d in [self.batches_dir] + [p.batches_dir for p in self.projects if p.batches_dir]:
            if d not in seen:
                seen.append(d)
        return seen

    @property
    def all_progress_dirs(self) -> list[Path]:
        seen: list[Path] = []
        for d in [self.progress_dir] + [p.progress_dir for p in self.projects if p.progress_dir]:
            if d not in seen:
                seen.append(d)
        return seen

    @property
    def graph_targets(self) -> dict[str, GraphDBConfig]:
        """Selectable graph databases for the Query Console, keyed by label."""
        out: dict[str, GraphDBConfig] = {}
        for proj in self.projects:
            if proj.graph_db is not None:
                out[proj.graph_db.label] = proj.graph_db
        return out

    @property
    def exports_dir(self) -> Path:
        return self.root / "labelwise_exports"


# ──────────────────────────────────────────────────────────────────────────
# Parsing
# ──────────────────────────────────────────────────────────────────────────
def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] in (None, ""):
        raise ConfigError(f"{where}: required key '{key}' is missing or empty.")
    return mapping[key]


def _positive_int(mapping: dict[str, Any], key: str, default: int, where: str) -> int:
    """Coerce a config value to a strictly positive int, or raise ConfigError.

    A bare ``int(...)`` here was a real bug: a typo'd value such as
    ``display_row_cap: lots`` raised an uncaught ``ValueError`` out of
    ``load_config``, which app.py's bootstrap does not catch (it catches
    ConfigError only), so the annotator got a raw Streamlit stack trace
    instead of the "Configuration error" page. Booleans are rejected too:
    ``display_row_cap: true`` would otherwise silently become 1.

    Strictly positive, because every setting using this is a count or a
    duration where zero or negative is meaningless. A negative
    ``display_row_cap`` in particular used to load fine and then render
    ``records[:-5]`` under a caption reading "Showing first -5 of 8 rows".
    """
    value = mapping.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError(
            f"{where}: '{key}' must be a positive whole number, got "
            f"{value!r} ({type(value).__name__})."
        )
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        raise ConfigError(
            f"{where}: '{key}' must be a positive whole number, got {value!r}."
        ) from None
    if parsed < 1:
        raise ConfigError(
            f"{where}: '{key}' must be a positive whole number (1 or more), "
            f"got {parsed}."
        )
    return parsed


def validate_gold_cache_mapping(parsed: Any, where: str, source: Any) -> dict:
    """Check a parsed gold cache is ``{id: {...}}`` and return it.

    Entry *shape* matters, not just the top-level container: a cache like
    ``{"1": "oops"}`` is valid JSON and a valid dict, and used to sail past
    startup validation only to blow up as
    ``AttributeError: 'str' object has no attribute 'get'`` deep inside the
    annotation result panel — at the moment an annotator opened that one
    item. Fail here instead, naming the offending id.
    """
    if not isinstance(parsed, dict):
        raise ConfigError(
            f"{where}: gold cache must be a JSON object mapping query id -> "
            f"result object, got {type(parsed).__name__} ({source})."
        )
    for key, value in parsed.items():
        if not isinstance(value, dict):
            raise ConfigError(
                f"{where}: gold cache entry for id '{key}' must be a JSON "
                f"object (with keys like 'records'/'error'/'elapsed'), got "
                f"{type(value).__name__}: {value!r} ({source})."
            )
    return parsed



def _resolve(raw: str, root: Path) -> Path:
    """Resolve a config path relative to the config file's directory.

    ``~`` is expanded so ``~/data/...`` works; absolute paths pass through.
    """
    p = Path(str(raw)).expanduser()
    return p if p.is_absolute() else (root / p)


def _parse_graph_db(raw: Any, where: str) -> GraphDBConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.graph_db must be a mapping (or omitted).")
    return GraphDBConfig(
        label=str(raw.get("label") or _require(raw, "uri", f"{where}.graph_db")),
        uri=str(_require(raw, "uri", f"{where}.graph_db")),
        user=str(raw.get("user", "neo4j")),
        password_env=str(_require(raw, "password_env", f"{where}.graph_db")),
        database=(str(raw["database"]) if raw.get("database") else None),
    )


def _parse_jury(raw: Any, root: Path, where: str) -> JuryConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}.jury_results must be a mapping (or omitted).")
    judges = raw.get("judges") or []
    if not isinstance(judges, list):
        raise ConfigError(f"{where}.jury_results.judges must be a list of names.")
    return JuryConfig(
        csv_path=_resolve(_require(raw, "csv", f"{where}.jury_results"), root),
        id_column=str(raw.get("id_column", "id")),
        verdict_column=str(raw.get("verdict_column", "jury_verdict")),
        judges=tuple(str(j) for j in judges),
        judge_verdict_template=str(raw.get("judge_verdict_template", "{judge}_verdict")),
        judge_reason_template=str(raw.get("judge_reason_template", "{judge}_reason")),
        title=str(raw.get("title", "LLM Jury Assessment")),
        hidden=bool(raw.get("hidden", False)),
        hidden_message=str(
            raw.get(
                "hidden_message",
                "LLM jury results are hidden for this batch during blind annotation.",
            )
        ),
    )


def _parse_schema_help(raw: Any, where: str) -> tuple[SchemaHelpSection, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{where}.schema_help must be a list of sections.")
    out = []
    for i, sec in enumerate(raw):
        if not isinstance(sec, dict):
            raise ConfigError(f"{where}.schema_help[{i}] must be a mapping.")
        out.append(
            SchemaHelpSection(
                title=str(sec.get("title", "")),
                body=str(sec.get("body", "")),
            )
        )
    return tuple(out)




def _parse_display(raw: Any, where: str) -> tuple[DisplayField, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(f"{where}.display must be a list of fields.")
    out = []
    for i, f in enumerate(raw):
        if isinstance(f, str):
            f = {"field": f}
        if not isinstance(f, dict) or not f.get("field"):
            raise ConfigError(f"{where}.display[{i}] must be a column name or a mapping with 'field'.")
        kind = str(f.get("as", "text"))
        if kind not in ("text", "markdown", "code", "json", "table", "chart"):
            raise ConfigError(f"{where}.display[{i}].as must be text, markdown, code, json, "
                              f"table or chart.")
        chart = f.get("chart") or {}
        if not isinstance(chart, dict):
            raise ConfigError(f"{where}.display[{i}].chart must be a mapping (kind, lines, ...).")
        out.append(DisplayField(str(f["field"]), str(f.get("label", "")), kind,
                                str(f.get("language", "")), bool(f.get("collapsed", False)),
                                chart))
    return tuple(out)


def _parse_project(raw: Any, root: Path, index: int, ws_id: str) -> ProjectConfig:
    if not isinstance(raw, dict):
        raise ConfigError(f"workspace '{ws_id}': projects[{index}] must be a mapping.")
    pid = str(_require(raw, "id", f"workspace '{ws_id}': projects[{index}]"))
    where = f"workspace '{ws_id}', project '{pid}'"
    if not _IDENT_RE.match(pid):
        raise ConfigError(
            f"{where}: id may contain only letters, digits, '_', '-', '.' "
            f"(it is used in filenames and widget keys)."
        )

    task = str(raw.get("task", "verdict"))
    if task not in TASKS:
        raise ConfigError(f"{where}: task must be one of {', '.join(TASKS)} (got '{task}').")
    spec = customise(BUILTIN[task], raw, where)

    gold = raw.get("gold_cache")
    gold_file = gold_dir = None
    shard_template = "{batch}_cache.json"
    if spec.view == "generic" or (spec.results_withheld and gold is None):
        # Generic tasks have no executed results; results-withheld tasks may omit them.
        gold = gold or {}
    if not isinstance(gold, dict):
        raise ConfigError(
            f"{where}: 'gold_cache' must be a mapping with either 'file:' "
            f"(single JSON) or 'shard_dir:' (one JSON per batch)."
        )
    gold_file, gold_dir = gold.get("file"), gold.get("shard_dir")
    shard_template = str(gold.get("shard_filename_template", shard_template))
    if spec.view == "generic" or spec.results_withheld:
        if gold_file and gold_dir:
            raise ConfigError(f"{where}.gold_cache: set at most one of 'file:' or 'shard_dir:'.")
    elif bool(gold_file) == bool(gold_dir):
        raise ConfigError(
            f"{where}.gold_cache: set exactly one of 'file:' or 'shard_dir:' "
            f"(got {'both' if gold_file else 'neither'})."
        )

    bench = raw.get("benchmark_csv")
    if spec.view != "generic" and not bench:
        raise ConfigError(f"{where}: required key 'benchmark_csv' is missing or empty.")

    checks = raw.get("checks") or {}
    if not isinstance(checks, dict):
        raise ConfigError(f"{where}: 'checks' must map item id -> expected answer.")

    return ProjectConfig(
        id=pid,
        name=str(raw.get("name", pid)),
        batch_prefix=str(raw.get("batch_prefix", "")),
        task_spec=spec,
        benchmark_csv=_resolve(bench, root) if bench else None,
        batches_dir=_resolve(raw["batches_dir"], root) if raw.get("batches_dir") else None,
        progress_dir=_resolve(raw["progress_dir"], root) if raw.get("progress_dir") else None,
        gold_cache_file=_resolve(gold_file, root) if gold_file else None,
        gold_cache_shard_dir=_resolve(gold_dir, root) if gold_dir else None,
        gold_cache_shard_template=shard_template,
        graph_db=_parse_graph_db(raw.get("graph_db"), where),
        jury=_parse_jury(raw.get("jury_results"), root, where),
        schema_help=_parse_schema_help(raw.get("schema_help"), where),
        is_default=bool(raw.get("default", False)),
        cypher_placeholder=str(raw.get("cypher_placeholder", "")),
        instructions=str(raw.get("instructions", "")),
        archived=bool(raw.get("archived", False)),
        ask_paraphrase=bool(raw.get("ask_paraphrase", spec.view != "generic")),
        display=_parse_display(raw.get("display"), where),
        checks={str(k): v for k, v in checks.items()},
        description=str(raw.get("description", "")),
        practice=bool(raw.get("practice", False)),
    )


def load_workspace(manifest: str | Path, *, display_row_cap: int = 300,
                   query_timeout_seconds: int = 30, archived_projects=()) -> WorkspaceConfig:
    """Parse a workspace manifest. Raises ConfigError with an actionable message."""
    path = Path(manifest).expanduser()
    if path.is_dir():
        path = manifest_in(path)
    if not path.exists():
        raise ConfigError(f"Workspace manifest not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Workspace manifest {path} is not valid YAML:\n{exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"Workspace manifest {path} must contain a YAML mapping.")

    root = path.parent.resolve()
    ws_raw = raw.get("workspace")
    if not isinstance(ws_raw, dict):
        raise ConfigError(f"{path}: must contain a 'workspace:' mapping.")
    ws_id = str(_require(ws_raw, "id", f"{path}: workspace"))
    if not _IDENT_RE.match(ws_id):
        raise ConfigError(f"{path}: workspace id may contain only letters, digits, '_', '-', '.'.")

    projects_raw = raw.get("projects")
    if not isinstance(projects_raw, list) or not projects_raw:
        raise ConfigError(f"workspace '{ws_id}': 'projects:' must be a non-empty list.")
    projects = tuple(_parse_project(p, root, i, ws_id) for i, p in enumerate(projects_raw))

    seen: set[str] = set()
    for proj in projects:
        if proj.id in seen:
            raise ConfigError(f"workspace '{ws_id}': duplicate project id '{proj.id}'.")
        seen.add(proj.id)
    defaults = [p.id for p in projects if p.is_default]
    if len(defaults) > 1:
        raise ConfigError(
            f"workspace '{ws_id}': more than one project is marked 'default: true': {defaults}."
        )
    prefixes = [p.batch_prefix for p in projects if p.batch_prefix]
    if len(set(prefixes)) != len(prefixes):
        raise ConfigError(f"workspace '{ws_id}': two projects declare the same batch_prefix: {prefixes}")

    archived_projects = set(archived_projects or ())
    if archived_projects:
        projects = tuple(
            ProjectConfig(**{**{f: getattr(p, f) for f in p.__dataclass_fields__},
                             "archived": True}) if p.id in archived_projects else p
            for p in projects
        )

    return WorkspaceConfig(
        id=ws_id,
        name=str(ws_raw.get("name", ws_id)),
        root=root,
        manifest=path.resolve(),
        batches_dir=_resolve(ws_raw.get("batches_dir", "batches"), root),
        progress_dir=_resolve(ws_raw.get("progress_dir", "progress"), root),
        assignments_file=_resolve(ws_raw.get("assignments_file", "labelwise_assignments.json"), root),
        owner=str(ws_raw.get("owner", "")),
        description=str(ws_raw.get("description", "")),
        archived=bool(ws_raw.get("archived", False)),
        projects=projects,
        display_row_cap=display_row_cap,
        query_timeout_seconds=query_timeout_seconds,
    )


def manifest_in(folder: Path) -> Path:
    return folder / MANIFEST_NAME
