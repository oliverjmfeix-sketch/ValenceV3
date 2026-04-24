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
    probe_query — (optional) custom idempotency probe. When set, used
                 instead of the generic `match $e isa {probe_type}` count.
                 The query must SELECT at least one variable and return
                 rows iff this specific seed has already loaded. Useful
                 when multiple seed files share a probe_type (e.g., both
                 questions.tql and rp_deontic_extraction_questions.tql
                 populate ontology_question — the generic count would
                 see >0 after the first loads and skip the second).
    """
    filename: str
    probe_type: str
    probe_query: str | None = None


# Seeds loaded into both database kinds.
SHARED_SEEDS: list[SeedFile] = [
    SeedFile("deontic_primitives_seed.tql", "object_class"),
    SeedFile("state_predicates_seed.tql", "state_predicate"),
    SeedFile("segment_types_seed.tql", "document_segment_type"),
    SeedFile("gold_questions_seed.tql", "gold_question"),
]

# Seeds loaded only into the extraction database.
#
# Order matters: v3 RP ontology first (categories, questions, annotations —
# drive extraction prompt assembly + per-basket attribute routing), then
# v4-specific projection infrastructure (mappings, specs, extraction questions).
#
# Per-seed probe_query disambiguates files that share a probe_type
# (ontology_question in particular — multiple seeds populate it).
EXTRACTION_ONLY_SEEDS: list[SeedFile] = [
    # ── v3 RP ontology needed for basket creation + attribute routing ─────────
    # concepts.tql intentionally skipped: its 1200-line single-insert block
    # triggers TypeDB 3.x INF11 type-inference edge cases. The 36 rp_v4_*
    # questions are scalar (string/boolean) and do not require concept
    # instances. Multiselect answers for v3 questions may degrade without
    # concepts, but extraction of per-basket attributes (the Part 5 goal)
    # does not depend on them.
    SeedFile(
        "questions.tql",
        probe_type="ontology_question",
        probe_query='match $q isa ontology_question, has question_id "rp_a1"; select $q;',
    ),
    SeedFile(
        "categories.tql",
        probe_type="ontology_category",
        probe_query='match $c isa ontology_category, has category_id "L"; select $c;',
    ),
    SeedFile(
        "seed_new_questions.tql",
        probe_type="ontology_question",
        probe_query='match $q isa ontology_question, has question_id "rp_g8"; select $q;',
    ),
    SeedFile(
        "seed_entity_list_questions.tql",
        probe_type="ontology_question",
        probe_query='match $q isa ontology_question, has question_id "rp_el_sweep_tiers"; select $q;',
    ),
    SeedFile(
        "seed_capacity_classifications.tql",
        probe_type="capacity_classification",
        probe_query='match $c isa capacity_classification; select $c;',
    ),
    SeedFile(
        "question_annotations.tql",
        probe_type="question_annotates_attribute",
        probe_query='match $r (question: $q) isa question_annotates_attribute, has target_entity_type "builder_basket"; select $r;',
    ),
    # ── v4-specific harness + projection infrastructure ───────────────────────
    SeedFile("segment_norm_expectations.tql", "segment_norm_expectation"),
    SeedFile("expected_norm_kinds.tql", "expected_norm_kind"),
    SeedFile("rp_deontic_mappings.tql", "deontic_mapping"),
    SeedFile("rp_condition_builders.tql", "condition_builder_spec"),
    SeedFile(
        "rp_deontic_extraction_questions.tql",
        probe_type="ontology_question",
        probe_query='match $q isa ontology_question, has question_id "rp_v4_F_cc"; select $q;',
    ),
    # Per-deal party instances — one file per deal. Duck Creek only for pilot.
    # Projection binds norm subjects to these instances by party_role match.
    # Adding a new deal = new seed file with the same shape, appended here.
    SeedFile(
        "duck_creek_parties_seed.tql",
        probe_type="party",
        probe_query='match $p isa party, has party_id "6e76ed06__borrower"; select $p;',
    ),
    # Segment prefix patterns — map norm.source_section → document_segment_type
    # at projection time. Loaded after segment_types_seed, annotates existing
    # segment instances via match-insert.
    SeedFile(
        "rp_segment_prefix_patterns.tql",
        probe_type="document_segment_type",
        probe_query='match $s isa document_segment_type, has segment_type_id "negative_cov_rp", has segment_prefix_pattern $p; select $s;',
    ),
    # Kind-name alignment patch — renames two legacy mapping target_norm_kind
    # values to match GT YAML / expected_norm_kinds seed. Fresh builds get
    # the corrected form directly from rp_deontic_mappings.tql (updated in
    # the same commit); this patch file only fires when loaded against a
    # previously-seeded valence_v4 with the legacy values.
    SeedFile(
        "rp_mapping_kind_fixes.tql",
        probe_type="deontic_mapping",
        probe_query='match $m isa deontic_mapping, has mapping_id "map_management_equity_basket", has target_norm_kind "management_equity_basket_permission"; select $m;',
    ),
    # Classification field config — per-field dimension relevance (Prompt 10 Fix 2)
    SeedFile(
        "classification_field_config_seed.tql",
        probe_type="classification_field_config",
    ),
]


def seed_files_for(kind: DatabaseKind) -> list[SeedFile]:
    """Return the list of SeedFile definitions for a given database kind."""
    seeds = list(SHARED_SEEDS)
    if kind == "extraction":
        seeds.extend(EXTRACTION_ONLY_SEEDS)
    return seeds


def _count_instances(driver, database_name: str, isa: str, probe_query: str | None = None) -> int:
    """Count rows matching either the generic type probe (`match $e isa {isa}`)
    or a custom probe_query (when supplied). Custom queries let a seed file
    probe a specific entity/relation unique to itself when the generic type
    is shared with an earlier-loading file."""
    tx = driver.transaction(database_name, TransactionType.READ)
    try:
        query = probe_query if probe_query else f"match $e isa {isa}; select $e;"
        result = tx.query(query).resolve()
        return len(list(result.as_concept_rows()))
    except Exception:
        # A probe_query referencing a type not yet in the schema fails with
        # "type not found" — treat that as "not present" (count=0).
        return 0
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

        existing = _count_instances(driver, database_name, seed.probe_type, seed.probe_query)
        if existing > 0:
            logger.info("  %s already seeded (%d matches) — skipping",
                        seed.filename, existing)
            counts[seed.filename] = existing
            continue

        t0 = time.perf_counter()
        ins_ok, mi_ok, total = _load_write_file(driver, database_name, seed_path)
        ms = (time.perf_counter() - t0) * 1000

        loaded = _count_instances(driver, database_name, seed.probe_type, seed.probe_query)
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
