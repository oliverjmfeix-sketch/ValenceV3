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
# ground-truth graph.
EXTRACTION_ONLY_SEEDS: list[SeedFile] = [
    SeedFile("segment_norm_expectations.tql", "segment_norm_expectation"),
    SeedFile("expected_norm_kinds.tql", "expected_norm_kind"),
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


def _load_write_file(driver, database_name: str, filepath: Path) -> None:
    tx = driver.transaction(database_name, TransactionType.WRITE)
    try:
        tx.query(filepath.read_text(encoding="utf-8")).resolve()
        tx.commit()
    except Exception:
        if tx.is_open():
            tx.close()
        raise


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
        _load_write_file(driver, database_name, seed_path)
        ms = (time.perf_counter() - t0) * 1000

        loaded = _count_instances(driver, database_name, seed.probe_type)
        counts[seed.filename] = loaded
        logger.info("  %s loaded in %.0f ms (%d %s instances)",
                    seed.filename, ms, loaded, seed.probe_type)

    if not skip_integrity_check:
        logger.info("  verifying state_predicate_id composite-key integrity")
        assert_state_predicate_ids_consistent(driver, database_name)
        logger.info("  integrity check OK")

    return counts
