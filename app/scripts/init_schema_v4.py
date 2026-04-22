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
from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType  # noqa: E402


DATA_DIR = REPO_ROOT / "app" / "data"
DOCS_DIR = REPO_ROOT / "docs"
SCHEMA_V3 = DATA_DIR / "schema_unified.tql"
SCHEMA_V4 = DATA_DIR / "schema_v4_deontic.tql"
SNAPSHOT_PRE = DOCS_DIR / "v4_schema_snapshot_pre_init.tql"
SNAPSHOT_POST = DOCS_DIR / "v4_schema_snapshot_post_init.tql"

EXPECTED_DB = "valence_v4"


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


def main() -> int:
    preflight()
    driver = connect()

    # ── pre-load snapshot ─────────────────────────────────────────────────────
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
