"""
Initialize TypeDB Schema for ValenceV3

Run this once after setting up TypeDB Cloud:
    python -m app.scripts.init_schema

This creates the database (if needed), loads schema, and seeds all data.
Loads all 11 TQL files in dependency order.
"""
import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Add parent to path so 'app' package resolves
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Load .env if present
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent.parent / ".env")

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

# Configuration from environment
TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")

# Data files — loaded in dependency order
DATA_DIR = Path(__file__).parent.parent / "data"

# 1. Schema definition (SCHEMA transaction)
SCHEMA_FILE = DATA_DIR / "schema_unified.tql"

# 2-3. Base data (single insert blocks, no deps)
CONCEPTS_FILE = DATA_DIR / "concepts.tql"
QUESTIONS_FILE = DATA_DIR / "questions.tql"

# 4-6. Category relations (mixed insert + match-insert, refs questions)
CATEGORIES_FILE = DATA_DIR / "categories.tql"
ONTOLOGY_EXPANDED_FILE = DATA_DIR / "ontology_expanded.tql"
CATEGORY_M_FILE = DATA_DIR / "ontology_category_m.tql"

# 7-10. Extraction metadata (multiple separate insert statements)
SEED_METADATA_FILE = DATA_DIR / "seed_extraction_metadata.tql"
RP_BASKET_METADATA_FILE = DATA_DIR / "rp_basket_metadata.tql"
RDP_BASKET_METADATA_FILE = DATA_DIR / "rdp_basket_metadata.tql"
INVESTMENT_PATHWAY_METADATA_FILE = DATA_DIR / "investment_pathway_metadata.tql"

# 11. V4 seed data (IP types, party types — multiple separate inserts)
SEED_V4_DATA_FILE = DATA_DIR / "seed_v4_data.tql"


def get_driver():
    """Get TypeDB 3.x driver."""
    address = TYPEDB_ADDRESS
    # Ensure https:// prefix for cloud
    if not address.startswith("http://") and not address.startswith("https://"):
        address = f"https://{address}"

    logger.info(f"Connecting to TypeDB: {address}")
    return TypeDB.driver(
        address,
        Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD),
        DriverOptions()
    )


def load_tql_file(filepath: Path) -> str:
    """Load and clean TQL file (strip comments and blank lines)."""
    content = filepath.read_text(encoding="utf-8")
    lines = []
    for line in content.split('\n'):
        stripped = line.strip()
        if stripped and not stripped.startswith('#'):
            lines.append(line)
    return '\n'.join(lines)


def _load_mixed_tql_file(driver, db_name: str, filepath: Path):
    """
    Load a TQL file that contains both insert and match-insert statements.
    Parses and executes them separately.
    """
    content = filepath.read_text(encoding="utf-8")
    lines = content.split('\n')
    insert_lines = []
    match_insert_statements = []
    current_statement = []
    in_insert_block = False
    in_match_block = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        if stripped == 'insert' or stripped.startswith('insert '):
            if in_match_block and current_statement:
                # This insert is the insert-clause of a match-insert pair
                current_statement.append(stripped)
                continue
            in_insert_block = True
            in_match_block = False
            insert_lines.append(stripped)
            continue

        if stripped.startswith('match ') or stripped == 'match':
            in_insert_block = False
            in_match_block = True
            if current_statement:
                match_insert_statements.append('\n'.join(current_statement))
            current_statement = [stripped]
            continue

        if in_insert_block:
            insert_lines.append(stripped)
        elif in_match_block:
            current_statement.append(stripped)

    if current_statement:
        match_insert_statements.append('\n'.join(current_statement))

    # Execute insert block
    if insert_lines:
        insert_tql = '\n'.join(insert_lines)
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(insert_tql).resolve()
            tx.commit()
            logger.info(f"  Executed insert block ({len(insert_lines)} lines)")
        except Exception as e:
            if tx.is_open():
                tx.close()
            error_msg = str(e).lower()
            if "already" in error_msg or "duplicate" in error_msg:
                logger.info("  Insert block already exists (skipping)")
            else:
                logger.warning(f"  Insert block error: {e}")

    # Execute match-insert statements one at a time
    created = 0
    skipped = 0
    failed = 0
    for stmt in match_insert_statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            created += 1
        except Exception as e:
            if tx.is_open():
                tx.close()
            error_msg = str(e).lower()
            if "already" in error_msg or "duplicate" in error_msg or "unique" in error_msg:
                skipped += 1
            else:
                failed += 1
                if failed <= 3:
                    logger.warning(f"  Match-insert error: {e}")

    logger.info(f"  Match-inserts: {created} created, {skipped} skipped, {failed} failed")


def _load_multi_insert_file(driver, db_name: str, filepath: Path):
    """
    Load a TQL file containing multiple separate insert statements.

    Files like seed_extraction_metadata.tql have:
        insert $em1 isa extraction_metadata, has metadata_id "...", ...;
        insert $em2 isa extraction_metadata, has metadata_id "...", ...;

    Each insert is executed as a separate transaction.
    """
    content = filepath.read_text(encoding="utf-8")
    lines = content.split('\n')

    # Collect statements: each starts with 'insert' and ends with ';'
    statements = []
    current = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.startswith('insert') and current:
            statements.append('\n'.join(current))
            current = [stripped]
        else:
            current.append(stripped)
    if current:
        statements.append('\n'.join(current))

    loaded = 0
    skipped = 0
    failed = 0
    for stmt in statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            loaded += 1
        except Exception as e:
            if tx.is_open():
                tx.close()
            error_msg = str(e).lower()
            if "already" in error_msg or "duplicate" in error_msg:
                skipped += 1
            else:
                failed += 1
                if failed <= 3:
                    logger.warning(f"  Insert error: {e}")

    logger.info(f"  {loaded} inserted, {skipped} already existed, {failed} failed ({len(statements)} total)")


def init_database():
    """Initialize TypeDB with ValenceV3 schema and all seed data."""
    logger.info("=" * 60)
    logger.info("ValenceV3 Schema Initialization (all 11 data files)")
    logger.info("=" * 60)

    driver = get_driver()

    try:
        # Check if database exists
        db_exists = driver.databases.contains(TYPEDB_DATABASE)
        logger.info(f"Database '{TYPEDB_DATABASE}' exists: {db_exists}")

        if db_exists:
            response = input("Drop and recreate? (yes/no): ").strip().lower()
            if response != 'yes':
                logger.info("Aborted. Use existing database.")
                return
            driver.databases.get(TYPEDB_DATABASE).delete()
            logger.info(f"Dropped database '{TYPEDB_DATABASE}'")

        # Create database
        driver.databases.create(TYPEDB_DATABASE)
        logger.info(f"Created database '{TYPEDB_DATABASE}'")

        # 1. Load schema
        logger.info("\n1. Loading schema_unified.tql...")
        if not SCHEMA_FILE.exists():
            logger.error(f"Schema file not found: {SCHEMA_FILE}")
            return

        schema_tql = load_tql_file(SCHEMA_FILE)
        tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
        try:
            tx.query(schema_tql).resolve()
            tx.commit()
        except Exception:
            if tx.is_open():
                tx.close()
            raise
        logger.info(f"   Loaded schema ({len(schema_tql)} chars)")

        # 2. Load concepts
        logger.info("\n2. Loading concepts.tql...")
        if CONCEPTS_FILE.exists():
            concepts_tql = load_tql_file(CONCEPTS_FILE)
            tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
            try:
                tx.query(concepts_tql).resolve()
                tx.commit()
                logger.info(f"   Loaded concepts ({len(concepts_tql)} chars)")
            except Exception as e:
                if tx.is_open():
                    tx.close()
                logger.warning(f"   Concepts: {e}")

        # 3. Load questions
        logger.info("\n3. Loading questions.tql...")
        if QUESTIONS_FILE.exists():
            questions_tql = load_tql_file(QUESTIONS_FILE)
            tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
            try:
                tx.query(questions_tql).resolve()
                tx.commit()
                logger.info(f"   Loaded questions ({len(questions_tql)} chars)")
            except Exception as e:
                if tx.is_open():
                    tx.close()
                logger.warning(f"   Questions: {e}")

        # 4. Load categories (mixed insert + match-insert)
        logger.info("\n4. Loading categories.tql...")
        if CATEGORIES_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CATEGORIES_FILE)

        # 5. Load expanded ontology
        logger.info("\n5. Loading ontology_expanded.tql...")
        if ONTOLOGY_EXPANDED_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, ONTOLOGY_EXPANDED_FILE)

        # 6. Load Category M ontology
        logger.info("\n6. Loading ontology_category_m.tql...")
        if CATEGORY_M_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CATEGORY_M_FILE)

        # 7. Load extraction metadata
        logger.info("\n7. Loading seed_extraction_metadata.tql...")
        if SEED_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, SEED_METADATA_FILE)

        # 8. Load RP basket metadata
        logger.info("\n8. Loading rp_basket_metadata.tql...")
        if RP_BASKET_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, RP_BASKET_METADATA_FILE)

        # 9. Load RDP basket metadata
        logger.info("\n9. Loading rdp_basket_metadata.tql...")
        if RDP_BASKET_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, RDP_BASKET_METADATA_FILE)

        # 10. Load investment pathway metadata
        logger.info("\n10. Loading investment_pathway_metadata.tql...")
        if INVESTMENT_PATHWAY_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, INVESTMENT_PATHWAY_METADATA_FILE)

        # 11. Load V4 seed data
        logger.info("\n11. Loading seed_v4_data.tql...")
        if SEED_V4_DATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, SEED_V4_DATA_FILE)

        # ═══════════════════════════════════════════════════════════════
        # Verification
        # ═══════════════════════════════════════════════════════════════
        logger.info("\n" + "=" * 60)
        logger.info("Verification Counts")
        logger.info("=" * 60)

        all_ok = True
        tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
        try:
            checks = [
                ("Concepts", "match $c isa concept; select $c;", 20),
                ("Questions", "match $q isa ontology_question; select $q;", 80),
                ("Categories", "match $cat isa ontology_category; select $cat;", 15),
                ("Extraction metadata", "match $em isa extraction_metadata; select $em;", 20),
                ("IP types", "match $ip isa ip_type; select $ip;", 5),
                ("Party types", "match $p isa restricted_party; select $p;", 3),
            ]
            for label, query, min_expected in checks:
                try:
                    result = list(tx.query(query).resolve().as_concept_rows())
                    count = len(result)
                    status = "OK" if count >= min_expected else "LOW"
                    if count < min_expected:
                        all_ok = False
                    logger.info(f"  [{status}] {label}: {count} (min expected: {min_expected})")
                except Exception as e:
                    all_ok = False
                    logger.warning(f"  [FAIL] {label}: query failed - {e}")
        finally:
            tx.close()

        logger.info("\n" + "=" * 60)
        if all_ok:
            logger.info("ValenceV3 schema initialization complete! All checks passed.")
        else:
            logger.warning("Initialization complete but some checks below threshold.")
        logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Initialization failed: {e}")
        raise
    finally:
        driver.close()


if __name__ == "__main__":
    init_database()
