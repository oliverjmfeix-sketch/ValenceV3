"""
Seed loading — shared between init_schema_v4 and load_ground_truth.

Centralizes seed file paths, per-kind membership (extraction vs ground_truth),
idempotency checks, and the post-seed state_predicate_id integrity check.
Each seed loads via a WRITE transaction; a probe_type tells the loader which
entity type to count as an idempotency signal (skip the seed if at least one
instance of that type already exists).

Kind semantics:
  - "extraction"   — valence_v4. Loads all 6 seeds, including the harness
                     expectation baselines (segment_norm_expectations,
                     expected_norm_kinds) that the validation harness queries
                     against this database.
  - "ground_truth" — valence_v4_ground_truth. Loads the 4 SHARED_SEEDS only.
                     Harness expectation seeds are intentionally excluded —
                     they are not consumed against the ground-truth database.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Literal, NamedTuple

from typedb.driver import TransactionType

from app.services.predicate_integrity import assert_state_predicate_ids_consistent

logger = logging.getLogger(__name__)

DatabaseKind = Literal["extraction", "ground_truth"]

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SEEDS_DIR = REPO_ROOT / "app" / "data"


class SeedFile(NamedTuple):
    """A seed file definition.

    filename   — basename under app/data/
    probe_type — entity type name to probe for idempotency (skip load when
                 at least one instance exists in the target database)
    """
    filename: str
    probe_type: str


# Seeds loaded into both database kinds.
SHARED_SEEDS: list[SeedFile] = [
    SeedFile("deontic_primitives_seed.tql", "object_class"),
    SeedFile("state_predicates_seed.tql", "state_predicate"),
    SeedFile("segment_types_seed.tql", "document_segment_type"),
    SeedFile("gold_questions_seed.tql", "gold_question"),
]

# Seeds loaded only into the extraction database. These encode harness-side
# validation baselines consumed against valence_v4, not against the
# ground-truth graph. rp_deontic_mappings + rp_condition_builders are
# projection-time infrastructure: the projection engine reads mappings to
# transform v3 extracted entities into v4 norms. Ground-truth DB is authored
# directly in YAML and does not need these mappings.
EXTRACTION_ONLY_SEEDS: list[SeedFile] = [
    SeedFile("segment_norm_expectations.tql", "segment_norm_expectation"),
    SeedFile("expected_norm_kinds.tql", "expected_norm_kind"),
    SeedFile("rp_deontic_mappings.tql", "deontic_mapping"),
    SeedFile("rp_condition_builders.tql", "condition_builder_spec"),
    SeedFile("rp_deontic_extraction_questions.tql", "ontology_question"),
]


def seed_files_for(kind: DatabaseKind) -> list[SeedFile]:
    """Return the list of SeedFile definitions for a given database kind."""
    seeds = list(SHARED_SEEDS)
    if kind == "extraction":
        seeds.extend(EXTRACTION_ONLY_SEEDS)
    return seeds


def _count_instances(driver, database_name: str, isa: str) -> int:
    tx = driver.transaction(database_name, TransactionType.READ)
    try:
        result = tx.query(f"match $e isa {isa}; select $e;").resolve()
        return len(list(result.as_concept_rows()))
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass


def _split_statements(content: str) -> tuple[list[str], list[str]]:
    """Split a TQL file into (pure_insert_statements, match_insert_statements).

    Mirrors app/scripts/init_schema.py:_load_mixed_tql_file parsing logic.
    TypeDB 3.x WRITE transactions must receive one match-insert per query;
    bundling multiple match-inserts into a single tx.query call silently
    executes only the first block. Splitting lets the loader run each
    statement in its own query.
    """
    insert_statements: list[str] = []
    match_insert_statements: list[str] = []
    current_lines: list[str] = []
    current_type: str | None = None  # "insert" | "match"
    has_insert_clause = False

    def flush() -> None:
        nonlocal current_lines, current_type, has_insert_clause
        if current_lines and current_type:
            stmt = "\n".join(current_lines)
            if current_type == "insert":
                insert_statements.append(stmt)
            else:
                match_insert_statements.append(stmt)
        current_lines = []
        current_type = None
        has_insert_clause = False

    for line in content.split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "match" or stripped.startswith("match "):
            flush()
            current_type = "match"
            current_lines = [stripped]
        elif stripped == "insert" or stripped.startswith("insert "):
            if current_type == "match" and not has_insert_clause:
                current_lines.append(stripped)
                has_insert_clause = True
            else:
                flush()
                current_type = "insert"
                current_lines = [stripped]
        else:
            if current_lines:
                current_lines.append(stripped)

    flush()
    return insert_statements, match_insert_statements


def _load_write_file(driver, database_name: str, filepath: Path) -> tuple[int, int, int]:
    """Load a TQL file by splitting into individual statements.

    Executes pure-insert statements first (entities before relations that
    reference them via match), then match-insert statements. Each statement
    runs in its own WRITE transaction. Returns (inserts_ok, match_inserts_ok,
    failures) — failures raised only if the caller asks (we log but continue
    so one bad statement doesn't silently skip the rest of a file).
    """
    content = filepath.read_text(encoding="utf-8")
    inserts, match_inserts = _split_statements(content)

    ins_ok = 0
    mi_ok = 0
    failures: list[tuple[str, str, str]] = []

    for stmt in inserts:
        tx = driver.transaction(database_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            ins_ok += 1
        except Exception as exc:  # noqa: BLE001
            if tx.is_open():
                tx.close()
            msg = str(exc).lower()
            if any(k in msg for k in ("already", "duplicate", "unique")):
                continue
            failures.append(("insert", stmt[:80], str(exc)[:160]))

    for stmt in match_inserts:
        tx = driver.transaction(database_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            mi_ok += 1
        except Exception as exc:  # noqa: BLE001
            if tx.is_open():
                tx.close()
            msg = str(exc).lower()
            if any(k in msg for k in ("already", "duplicate", "unique")):
                continue
            failures.append(("match-insert", stmt[:80], str(exc)[:160]))

    if failures:
        for kind, snippet, err in failures[:5]:
            logger.warning("%s failure: %s ... error: %s", kind, snippet, err)
        raise RuntimeError(
            f"Failed to load {len(failures)} statements from {filepath.name}; "
            f"first errors logged above"
        )

    return ins_ok, mi_ok, len(inserts) + len(match_inserts)


def load_seeds(
    driver,
    database_name: str,
    kind: DatabaseKind,
    *,
    skip_integrity_check: bool = False,
) -> dict[str, int]:
    """Load all seeds appropriate for the given database kind.

    Each seed is loaded in a fresh WRITE transaction. Seeds are skipped when
    at least one instance of their probe_type already exists in the target
    database (idempotent re-runs). After every seed is processed, the
    state_predicate_id composite-key integrity check runs to catch drift
    between the seed's stored ids and the construction rule in
    app/services/predicate_id.py.

    Returns a dict mapping seed filename → post-load instance count for the
    probe type (for reporting).
    """
    counts: dict[str, int] = {}

    for seed in seed_files_for(kind):
        seed_path = SEEDS_DIR / seed.filename
        if not seed_path.exists():
            raise FileNotFoundError(f"seed file missing: {seed_path}")

        existing = _count_instances(driver, database_name, seed.probe_type)
        if existing > 0:
            logger.info("  %s already seeded (%d %s instances) — skipping",
                        seed.filename, existing, seed.probe_type)
            counts[seed.filename] = existing
            continue

        t0 = time.perf_counter()
        ins_ok, mi_ok, total = _load_write_file(driver, database_name, seed_path)
        ms = (time.perf_counter() - t0) * 1000

        loaded = _count_instances(driver, database_name, seed.probe_type)
        counts[seed.filename] = loaded
        logger.info(
            "  %s loaded in %.0f ms (%d %s instances; %d/%d statements: %d pure + %d match-insert)",
            seed.filename, ms, loaded, seed.probe_type,
            ins_ok + mi_ok, total, ins_ok, mi_ok,
        )

    if not skip_integrity_check:
        logger.info("  verifying state_predicate_id composite-key integrity")
        assert_state_predicate_ids_consistent(driver, database_name)
        logger.info("  integrity check OK")

    return counts
