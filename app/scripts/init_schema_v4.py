"""
Initialize v4 deontic TypeDB schema into the valence_v4 database.

Usage:
    py -3.12 -m app.scripts.init_schema_v4

Pipeline:
    1. Preflight — confirm settings.typedb_database == "valence_v4"
    2. Connect to TypeDB Cloud using .env credentials
    3. Pre-load snapshot — export current valence_v4 schema if it exists, else note absence
    4. Create valence_v4 database if missing
    5. SCHEMA transaction: load app/data/schema_unified.tql (v3's base schema)
    6. SCHEMA transaction: load app/data/schema_v4_deontic.tql (the deontic overlay)
    7. Post-load snapshot — export full post-init schema

v3 ontology data (questions, synthesis_guidance, concepts, etc.) is NOT loaded:
v4 will replace the guidance layer, and v4-specific question additions come in
later prompts.

This script must be run *after* docs/v4_deontic_architecture.md is committed —
schema loading mutates the live database, so the architecture contract must be
stable first. The pre/post snapshot files in docs/ are committed alongside this
script so schema drift is visible in git.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("init_schema_v4")

# Resolve repo root and import app.config
REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

# Prefer the canonical main-repo .env (worktree may lack its own)
_main_env = Path("C:/Users/olive/ValenceV3/.env")
if _main_env.exists():
    load_dotenv(_main_env, override=False)
load_dotenv(REPO_ROOT / ".env", override=False)

from app.config import settings  # noqa: E402
from app.services.seed_loader import load_seeds  # noqa: E402
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402


DATA_DIR = REPO_ROOT / "app" / "data"
DOCS_DIR = REPO_ROOT / "docs"
SCHEMA_V3 = DATA_DIR / "schema_unified.tql"
SCHEMA_V4 = DATA_DIR / "schema_v4_deontic.tql"
SNAPSHOT_PRE = DOCS_DIR / "v4_schema_snapshot_pre_init.tql"
SNAPSHOT_POST = DOCS_DIR / "v4_schema_snapshot_post_init.tql"

# Function files — loaded in dependency order. predicate_holds must exist
# before condition_holds; condition_holds before norm_is_in_force; etc.
FUNCTION_FILES = [
    DATA_DIR / "deontic_condition_functions.tql",
    DATA_DIR / "deontic_norm_functions.tql",
    DATA_DIR / "deontic_capacity_functions.tql",
    DATA_DIR / "deontic_pathway_functions.tql",
    DATA_DIR / "deontic_validation_functions.tql",
    DATA_DIR / "deontic_pattern_functions.tql",
]

EXPECTED_DB = "valence_v4"

# The 18 singleton primitive types seeded by deontic_primitives_seed.tql.
# 9 concrete object_class subtypes + 9 concrete action_class subtypes.
# make_restricted_payment and instrument_class are abstract — no instance.
CONCRETE_OBJECT_CLASSES = [
    "cash",
    "business_division",
    "unrestricted_subsidiary_equity_or_assets",
    "equity_interest",
    "holdco_equity",
    "restricted_sub_equity",
    "unrestricted_sub_equity",
    "subordinated_debt_instrument",
    "material_intellectual_property",
]
CONCRETE_ACTION_CLASSES = [
    "make_dividend_payment",
    "repurchase_equity",
    "make_tax_distribution",
    "pay_holdco_overhead",
    "pay_subordinated_debt",
    "make_investment",
    "designate_unrestricted_subsidiary",
    "make_intercompany_payment",
    "transfer_material_intellectual_property",
]


def preflight() -> str:
    """Abort if .env is not pointed at valence_v4."""
    db = settings.typedb_database
    if db != EXPECTED_DB:
        logger.error("=" * 70)
        logger.error("PREFLIGHT FAILED: settings.typedb_database is %r", db)
        logger.error("Expected: %r", EXPECTED_DB)
        logger.error("Check .env or app/config.py — refusing to load v4 schema")
        logger.error("into the wrong database.")
        logger.error("=" * 70)
        raise SystemExit(2)
    logger.info("Preflight OK: target database is %r", db)
    return db


def connect():
    addr = settings.normalized_typedb_address
    logger.info("Connecting to TypeDB at %s", addr)
    driver = TypeDB.driver(
        addr,
        Credentials(settings.typedb_username, settings.typedb_password),
        DriverOptions(),
    )
    logger.info("Driver connected")
    return driver


def export_schema(driver, db_name: str) -> str | None:
    """Export the full schema of db_name as TQL, or None if db does not exist.

    Uses the 3.x driver's database-object schema() method when available;
    falls back to 'match ... select' introspection if the method is absent.
    """
    if not driver.databases.contains(db_name):
        return None

    db = driver.databases.get(db_name)
    # Preferred path: Database.schema() returns TQL as string in 3.x driver
    schema_text: str | None = None
    try:
        schema_text = db.schema()
    except AttributeError:
        logger.debug("Database.schema() unavailable; using introspection fallback")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Database.schema() raised %s: %s — falling back", type(exc).__name__, exc)

    if schema_text is not None:
        return schema_text

    # Fallback: simple type-listing introspection. Captures less detail than
    # a true schema dump, but useful as a human-readable reference.
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        rows = list(
            tx.query("match $t sub $parent; select $t, $parent;").resolve().as_concept_rows()
        )
        lines = ["# Introspection fallback — not a true schema dump.",
                 "# Each line: <child_type> sub <parent_type>", ""]
        pairs = sorted({
            (r.get("t").get_label(), r.get("parent").get_label()) for r in rows
        })
        for child, parent in pairs:
            lines.append(f"{child} sub {parent};")
        return "\n".join(lines) + "\n"
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass


def write_snapshot(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    logger.info("Snapshot written: %s  (%d bytes)", path.name, path.stat().st_size)


def load_schema_file(driver, db_name: str, filepath: Path) -> None:
    """Load a TQL schema file as a single SCHEMA transaction."""
    if not filepath.exists():
        raise FileNotFoundError(f"Schema file missing: {filepath}")
    logger.info("Loading schema file: %s", filepath.name)
    content = filepath.read_text(encoding="utf-8")

    tx = driver.transaction(db_name, TransactionType.SCHEMA)
    t0 = time.perf_counter()
    try:
        tx.query(content).resolve()
        tx.commit()
        ms = (time.perf_counter() - t0) * 1000
        logger.info("  %s committed in %.0f ms", filepath.name, ms)
    except Exception:
        if tx.is_open():
            tx.close()
        raise


def count_primitive_instances(driver, db_name: str) -> dict[str, int]:
    """Return per-type instance counts for the concrete action/object classes.

    Uses `isa!` (exact-type match) rather than `isa` (polymorphic match) so
    a query for `equity_interest` doesn't also pick up instances of its
    subtypes (holdco_equity, restricted_sub_equity, unrestricted_sub_equity).
    The 9 concrete object-class singletons then each count exactly once.
    """
    counts: dict[str, int] = {}
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        for label in CONCRETE_OBJECT_CLASSES + CONCRETE_ACTION_CLASSES:
            result = tx.query(f"match $e isa! {label}; select $e;").resolve()
            counts[label] = len(list(result.as_concept_rows()))
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass
    return counts


def verify_types(driver, db_name: str) -> list[str]:
    """Return sorted list of all labelled types (entity/relation/attribute) in the db.

    Uses schema-reflection queries (`match entity $t` etc.) rather than instance
    queries (`isa! $type`) so the enumeration works even when no data has been
    loaded yet.
    """
    labels: set[str] = set()
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        for kind in ("entity", "relation", "attribute"):
            result = tx.query(f"match {kind} $t; select $t;").resolve()
            for row in result.as_concept_rows():
                labels.add(row.get("t").get_label())
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass
    return sorted(labels)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Initialize the valence_v4 database.")
    p.add_argument(
        "--seed-primitives",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Seed the 18 concrete action/object class singletons after schema load (default: true)",
    )
    p.add_argument(
        "--load-functions",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Load the six deontic_*_functions.tql files (default: true)",
    )
    p.add_argument(
        "--preserve-extraction",
        action="store_true",
        help="Explicitly permit running against a valence_v4 that contains extracted v3 "
             "entities (rp_basket instances, etc.). Without this or --schema-only, the "
             "script aborts if extraction data is present to avoid destructive re-runs "
             "that would erase a $15 extraction artifact.",
    )
    p.add_argument(
        "--schema-only",
        action="store_true",
        help="Update schema + seeds only. Skips deontic function loading (functions "
             "can't be redefined in TypeDB 3.x — FUN5 error). Safe to re-run against "
             "a database with extraction data.",
    )
    return p.parse_args()


def _extraction_entity_count(driver, db_name: str) -> int:
    """Return count of v3 extracted entity instances.

    Uses rp_basket as the primary sentinel — it's a polymorphic parent over
    9 concrete subtypes. A non-zero count means the $12.95 Part 5 extraction
    (or a subsequent re-extraction) is present and must not be destroyed
    without explicit opt-in.
    """
    if not driver.databases.contains(db_name):
        return 0
    tx = driver.transaction(db_name, TransactionType.READ)
    try:
        result = tx.query("match $b isa rp_basket; select $b;").resolve()
        return len(list(result.as_concept_rows()))
    except Exception:
        return 0
    finally:
        try:
            if tx.is_open():
                tx.close()
        except Exception:  # noqa: BLE001
            pass


def guard_extraction_data(driver, db_name: str, args: argparse.Namespace) -> None:
    """Refuse to proceed if extraction data is present and no explicit
    opt-in flag has been passed. Prevents accidental destruction of an
    expensive extraction artifact by a routine schema-update re-run.
    """
    count = _extraction_entity_count(driver, db_name)
    if count == 0:
        return

    if args.preserve_extraction or args.schema_only:
        logger.info(
            "Extraction data detected in %s (%d rp_basket instances) — "
            "proceeding with %s mode; schema/seed additions applied in-place.",
            db_name, count,
            "--schema-only" if args.schema_only else "--preserve-extraction",
        )
        return

    logger.error("=" * 72)
    logger.error("REFUSING TO PROCEED — extraction data present in %r", db_name)
    logger.error("  Found %d rp_basket instances.", count)
    logger.error("  init_schema_v4 would re-load function files (which fails with")
    logger.error("  FUN5 duplicate-function errors) and potentially disturb the")
    logger.error("  extraction artifact. Pass one of:")
    logger.error("")
    logger.error("    --schema-only          Update schema/seeds only; skip functions.")
    logger.error("                           Safe for normal re-runs after extraction.")
    logger.error("")
    logger.error("    --preserve-extraction  Proceed with full init, including function")
    logger.error("                           re-load (will fail on FUN5 unless the")
    logger.error("                           database was rebuilt — use with care).")
    logger.error("=" * 72)
    raise SystemExit(4)


def main() -> int:
    args = parse_args()
    preflight()
    driver = connect()

    # ── extraction-data safeguard ─────────────────────────────────────────────
    # Abort if rp_basket instances are present and the caller hasn't explicitly
    # opted into either --schema-only (safe re-run) or --preserve-extraction.
    guard_extraction_data(driver, EXPECTED_DB, args)

    # ── pre-load snapshot (one-shot: preserve the original "before v4 existed"
    #    snapshot; later re-runs keep it frozen) ────────────────────────────────
    if SNAPSHOT_PRE.exists():
        logger.info(
            "Pre-init snapshot already on disk (%s) — preserving as the historical record",
            SNAPSHOT_PRE.name,
        )
    else:
        logger.info("Exporting pre-init schema snapshot")
        pre_schema = export_schema(driver, EXPECTED_DB)
        if pre_schema is None:
            write_snapshot(
                SNAPSHOT_PRE,
                f"# database {EXPECTED_DB!r} did not exist at pre-init time — no schema to dump.\n",
            )
        else:
            write_snapshot(SNAPSHOT_PRE, pre_schema)

    # ── create db if missing ──────────────────────────────────────────────────
    if not driver.databases.contains(EXPECTED_DB):
        logger.info("Database %r does not exist — creating", EXPECTED_DB)
        driver.databases.create(EXPECTED_DB)
        logger.info("Created database %r", EXPECTED_DB)
    else:
        logger.info("Database %r already exists", EXPECTED_DB)

    # ── load schemas ──────────────────────────────────────────────────────────
    load_schema_file(driver, EXPECTED_DB, SCHEMA_V3)
    load_schema_file(driver, EXPECTED_DB, SCHEMA_V4)

    # ── load deontic functions (SCHEMA transaction per file, in dep order) ────
    # --schema-only implies skipping function re-load (they can't be redefined
    # in TypeDB 3.x; already-loaded functions trigger FUN5).
    if args.load_functions and not args.schema_only:
        logger.info("Loading %d deontic function files", len(FUNCTION_FILES))
        for fn_file in FUNCTION_FILES:
            load_schema_file(driver, EXPECTED_DB, fn_file)
    else:
        logger.info(
            "Skipping deontic function load (%s)",
            "--schema-only" if args.schema_only else "--no-load-functions",
        )

    # ── seed all 6 extraction-kind seeds via the shared loader ────────────────
    # load_seeds() is idempotent (per-seed probe) and runs the post-seed
    # state_predicate_id composite-key integrity check. SHARED_SEEDS +
    # EXTRACTION_ONLY_SEEDS = 4 + 2 = 6 files.
    if args.seed_primitives:
        logger.info("Loading seeds (kind=extraction)")
        seed_counts = load_seeds(driver, EXPECTED_DB, kind="extraction")
        logger.info("Seed counts: %s", seed_counts)

        # Additional per-singleton verification specific to the primitives seed.
        counts = count_primitive_instances(driver, EXPECTED_DB)
        obj_total = sum(v for k, v in counts.items() if k in CONCRETE_OBJECT_CLASSES)
        act_total = sum(v for k, v in counts.items() if k in CONCRETE_ACTION_CLASSES)
        logger.info("  object_class singletons: %d (expected %d)", obj_total, len(CONCRETE_OBJECT_CLASSES))
        logger.info("  action_class singletons: %d (expected %d)", act_total, len(CONCRETE_ACTION_CLASSES))
        bad = {k: v for k, v in counts.items() if v != 1}
        if bad:
            logger.warning("Primitive count drift (expected 1 each): %s", bad)
    else:
        logger.info("Skipping seed loading (--no-seed-primitives)")

    # ── post-load snapshot ────────────────────────────────────────────────────
    logger.info("Exporting post-init schema snapshot")
    post_schema = export_schema(driver, EXPECTED_DB)
    if post_schema is None:
        logger.error("Post-init schema export returned None — unexpected")
        return 3
    write_snapshot(SNAPSHOT_POST, post_schema)

    # ── minimal verification ──────────────────────────────────────────────────
    logger.info("Enumerating loaded types")
    labels = verify_types(driver, EXPECTED_DB)
    logger.info("  %d distinct types present", len(labels))
    deontic_core = {
        "party", "action_class", "object_class", "instrument_class",
        "state_predicate", "condition", "norm", "defeater",
        "event_instance", "violation_consequent",
    }
    missing = sorted(t for t in deontic_core if t not in labels)
    if missing:
        logger.warning("Core deontic types MISSING: %s", missing)
    else:
        logger.info("  All 10 core deontic types present")

    try:
        driver.close()
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
