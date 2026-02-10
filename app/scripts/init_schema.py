"""
Initialize TypeDB Schema for ValenceV3

Run this once after setting up TypeDB Cloud:
    python -m app.scripts.init_schema

This creates the database (if needed), loads schema, and seeds all data.
Loads all 16 TQL files in dependency order.
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

# 12-14. J.Crew deep analysis (concepts, questions, rules)
JCREW_CONCEPTS_FILE = DATA_DIR / "jcrew_concepts_seed.tql"
JCREW_QUESTIONS_FILE = DATA_DIR / "jcrew_questions_seed.tql"
JCREW_RULES_FILE = DATA_DIR / "jcrew_rules.tql"

# 15-16. MFN extended data
MFN_CONCEPTS_EXTENDED_FILE = DATA_DIR / "mfn_concepts_extended.tql"
MFN_QUESTIONS_FILE = DATA_DIR / "mfn_ontology_questions.tql"


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
    Load a TQL file that contains both standalone insert and match-insert statements.

    Handles files like ontology_expanded.tql where each question is a separate
    multi-line `insert` statement. Parses into individual statements, executes
    ALL standalone inserts first (so entities exist), then match-inserts.
    """
    content = filepath.read_text(encoding="utf-8")
    lines = content.split('\n')

    insert_statements = []   # standalone insert statements
    match_insert_statements = []  # match ... insert ... pairs
    current_lines = []
    current_type = None  # 'insert' or 'match'

    def flush():
        nonlocal current_lines, current_type
        if current_lines and current_type:
            stmt = '\n'.join(current_lines)
            if current_type == 'insert':
                insert_statements.append(stmt)
            elif current_type == 'match':
                match_insert_statements.append(stmt)
        current_lines = []
        current_type = None

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        if stripped.startswith('match ') or stripped == 'match':
            flush()
            current_type = 'match'
            current_lines = [stripped]
        elif stripped == 'insert' or stripped.startswith('insert '):
            if current_type == 'match':
                # This insert is the INSERT clause of a match-insert pair
                current_lines.append(stripped)
            else:
                # New standalone insert statement
                flush()
                current_type = 'insert'
                current_lines = [stripped]
        else:
            # Continuation line
            if current_lines:
                current_lines.append(stripped)

    flush()

    # Phase 1: Execute standalone inserts one at a time
    ins_ok = 0
    ins_skip = 0
    ins_fail = 0
    for stmt in insert_statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            ins_ok += 1
        except Exception as e:
            if tx.is_open():
                tx.close()
            error_msg = str(e).lower()
            if "already" in error_msg or "duplicate" in error_msg or "unique" in error_msg:
                ins_skip += 1
            else:
                ins_fail += 1
                if ins_fail <= 3:
                    logger.warning(f"  Insert error: {e}")

    logger.info(f"  Inserts: {ins_ok} created, {ins_skip} existed, {ins_fail} failed ({len(insert_statements)} total)")

    # Phase 2: Execute match-insert statements one at a time
    mi_ok = 0
    mi_skip = 0
    mi_fail = 0
    for stmt in match_insert_statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            mi_ok += 1
        except Exception as e:
            if tx.is_open():
                tx.close()
            error_msg = str(e).lower()
            if "already" in error_msg or "duplicate" in error_msg or "unique" in error_msg:
                mi_skip += 1
            else:
                mi_fail += 1
                if mi_fail <= 3:
                    logger.warning(f"  Match-insert error: {e}")

    logger.info(f"  Match-inserts: {mi_ok} created, {mi_skip} existed, {mi_fail} failed ({len(match_insert_statements)} total)")


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
    logger.info("ValenceV3 Schema Initialization (all 16 data files)")
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

        # 3. Load J.Crew concepts
        logger.info("\n3. Loading jcrew_concepts_seed.tql...")
        if JCREW_CONCEPTS_FILE.exists():
            jcrew_concepts_tql = load_tql_file(JCREW_CONCEPTS_FILE)
            tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
            try:
                tx.query(jcrew_concepts_tql).resolve()
                tx.commit()
                logger.info(f"   Loaded J.Crew concepts ({len(jcrew_concepts_tql)} chars)")
            except Exception as e:
                if tx.is_open():
                    tx.close()
                logger.warning(f"   J.Crew concepts: {e}")

        # 4. Load questions
        logger.info("\n4. Loading questions.tql...")
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

        # 5. Load categories (mixed insert + match-insert)
        logger.info("\n5. Loading categories.tql...")
        if CATEGORIES_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CATEGORIES_FILE)

        # 6. Load expanded ontology
        logger.info("\n6. Loading ontology_expanded.tql...")
        if ONTOLOGY_EXPANDED_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, ONTOLOGY_EXPANDED_FILE)

        # 7. Load Category M ontology
        logger.info("\n7. Loading ontology_category_m.tql...")
        if CATEGORY_M_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CATEGORY_M_FILE)

        # 8. Load J.Crew questions (mixed insert + match-insert)
        logger.info("\n8. Loading jcrew_questions_seed.tql...")
        if JCREW_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, JCREW_QUESTIONS_FILE)

        # 9. Load extraction metadata
        logger.info("\n9. Loading seed_extraction_metadata.tql...")
        if SEED_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, SEED_METADATA_FILE)

        # 10. Load RP basket metadata
        logger.info("\n10. Loading rp_basket_metadata.tql...")
        if RP_BASKET_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, RP_BASKET_METADATA_FILE)

        # 11. Load RDP basket metadata
        logger.info("\n11. Loading rdp_basket_metadata.tql...")
        if RDP_BASKET_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, RDP_BASKET_METADATA_FILE)

        # 12. Load investment pathway metadata
        logger.info("\n12. Loading investment_pathway_metadata.tql...")
        if INVESTMENT_PATHWAY_METADATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, INVESTMENT_PATHWAY_METADATA_FILE)

        # 13. Load V4 seed data
        logger.info("\n13. Loading seed_v4_data.tql...")
        if SEED_V4_DATA_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, SEED_V4_DATA_FILE)

        # 14. Load J.Crew inference rules (SCHEMA transaction — rules are schema-level)
        logger.info("\n14. Loading jcrew_rules.tql...")
        if JCREW_RULES_FILE.exists():
            rules_tql = load_tql_file(JCREW_RULES_FILE)
            tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
            try:
                tx.query(rules_tql).resolve()
                tx.commit()
                logger.info(f"   Loaded J.Crew rules ({len(rules_tql)} chars)")
            except Exception as e:
                if tx.is_open():
                    tx.close()
                logger.warning(f"   J.Crew rules: {e}")
                logger.warning("   NOTE: TypeDB 3.x rule syntax may need adaptation. Rules saved for reference.")

        # 15. Load MFN extended concepts (after concepts.tql)
        logger.info("\n15. Loading mfn_concepts_extended.tql...")
        if MFN_CONCEPTS_EXTENDED_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, MFN_CONCEPTS_EXTENDED_FILE)

        # 16. Load MFN ontology questions (after all concepts and questions)
        logger.info("\n16. Loading mfn_ontology_questions.tql...")
        if MFN_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, MFN_QUESTIONS_FILE)

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
                ("Concepts (incl J.Crew + MFN)", "match $c isa concept; select $c;", 95),
                ("Questions (incl J.Crew + MFN)", "match $q isa ontology_question; select $q;", 190),
                ("Categories (incl JC1-3, MFN1-6)", "match $cat isa ontology_category; select $cat;", 26),
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

        # Question count per category
        logger.info("\nQuestions per category:")
        tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)
        try:
            cat_query = """
                match
                    $q isa ontology_question, has question_id $qid;
                    (category: $cat, question: $q) isa category_has_question;
                    $cat has category_id $cid;
                select $qid, $cid;
            """
            cat_result = list(tx.query(cat_query).resolve().as_concept_rows())
            from collections import Counter
            cat_counts = Counter()
            for row in cat_result:
                cid = row.get("cid").as_attribute().get_value()
                cat_counts[cid] += 1
            for cid in sorted(cat_counts):
                logger.info(f"  {cid}: {cat_counts[cid]}")
            logger.info(f"  TOTAL with category relation: {sum(cat_counts.values())}")
        except Exception as e:
            logger.warning(f"  Category count query failed: {e}")
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
