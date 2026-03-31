"""
Initialize TypeDB Schema for ValenceV3

Run this once after setting up TypeDB Cloud:
    python -m app.scripts.init_schema

This creates the database (if needed), loads schema, and seeds all data.
Loads all TQL files in dependency order (19 steps).
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

# 4-5. Category relations + J.Crew (mixed insert + match-insert)
CATEGORIES_FILE = DATA_DIR / "categories.tql"
JCREW_CONCEPTS_FILE = DATA_DIR / "jcrew_concepts_seed.tql"
JCREW_QUESTIONS_FILE = DATA_DIR / "jcrew_questions_seed.tql"

# 6. Concept → entity mapping
CONCEPT_ENTITY_MAPPING_FILE = DATA_DIR / "seed_concept_entity_mapping.tql"

# 7. MFN ontology questions
MFN_QUESTIONS_FILE = DATA_DIR / "mfn_ontology_questions.tql"

# 8. Document segmentation types
SEGMENT_TYPES_FILE = DATA_DIR / "segment_types_seed.tql"

# 9b. DI reference entities
DI_REFERENCE_ENTITIES_FILE = DATA_DIR / "seed_di_reference_entities.tql"

# 9-16. Seed data (questions, mappings, annotations, guidance)
NEW_QUESTIONS_FILE = DATA_DIR / "seed_new_questions.tql"
ENTITY_LIST_QUESTIONS_FILE = DATA_DIR / "seed_entity_list_questions.tql"
CROSS_COVENANT_MAPPINGS_FILE = DATA_DIR / "seed_cross_covenant_mappings.tql"
CAPACITY_CLASSIFICATIONS_FILE = DATA_DIR / "seed_capacity_classifications.tql"
QUESTION_ANNOTATIONS_FILE = DATA_DIR / "question_annotations.tql"
MFN_ANNOTATIONS_FILE = DATA_DIR / "seed_mfn_annotations.tql"
MFN_ENTITY_LIST_QUESTIONS_FILE = DATA_DIR / "seed_mfn_entity_list_questions.tql"
SYNTHESIS_GUIDANCE_FILE = DATA_DIR / "seed_synthesis_guidance.tql"

# 17. Functions (SCHEMA transaction)
ANNOTATION_FUNCTIONS_FILE = DATA_DIR / "annotation_functions.tql"


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

    Handles files like questions.tql where each question is a separate
    multi-line `insert` statement. Parses into individual statements, executes
    ALL standalone inserts first (so entities exist), then match-inserts.
    """
    content = filepath.read_text(encoding="utf-8")
    lines = content.split('\n')

    insert_statements = []   # standalone insert statements
    match_insert_statements = []  # match ... insert ... pairs
    current_lines = []
    current_type = None  # 'insert' or 'match'
    has_insert_clause = False  # tracks whether a match block already has its insert clause

    def flush():
        nonlocal current_lines, current_type, has_insert_clause
        if current_lines and current_type:
            stmt = '\n'.join(current_lines)
            if current_type == 'insert':
                insert_statements.append(stmt)
            elif current_type == 'match':
                match_insert_statements.append(stmt)
        current_lines = []
        current_type = None
        has_insert_clause = False

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        if stripped.startswith('match ') or stripped == 'match':
            flush()
            current_type = 'match'
            current_lines = [stripped]
        elif stripped == 'insert' or stripped.startswith('insert '):
            if current_type == 'match' and not has_insert_clause:
                # This insert is the INSERT clause of a match-insert pair
                current_lines.append(stripped)
                has_insert_clause = True
            else:
                # New standalone insert statement (or match-insert already complete)
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
    force = "--force" in sys.argv

    logger.info("=" * 60)
    logger.info("ValenceV3 Schema Initialization (19 steps)")
    logger.info("=" * 60)

    driver = get_driver()

    try:
        # Check if database exists
        db_exists = driver.databases.contains(TYPEDB_DATABASE)
        logger.info(f"Database '{TYPEDB_DATABASE}' exists: {db_exists}")

        if db_exists:
            if not force:
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

        # 4. Load questions (mixed: inline insert block + match-insert for expanded questions)
        logger.info("\n4. Loading questions.tql...")
        if QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, QUESTIONS_FILE)

        # 5. Load categories (mixed insert + match-insert)
        logger.info("\n5. Loading categories.tql...")
        if CATEGORIES_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CATEGORIES_FILE)

        # 6. Load J.Crew questions (mixed insert + match-insert)
        logger.info("\n6. Loading jcrew_questions_seed.tql...")
        if JCREW_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, JCREW_QUESTIONS_FILE)

        # 7. Load concept → entity boolean mapping
        logger.info("\n7. Loading seed_concept_entity_mapping.tql...")
        if CONCEPT_ENTITY_MAPPING_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CONCEPT_ENTITY_MAPPING_FILE)

        # 8. Load MFN ontology questions (after all concepts and questions)
        logger.info("\n8. Loading mfn_ontology_questions.tql...")
        if MFN_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, MFN_QUESTIONS_FILE)

        # 9. Load document segment types
        logger.info("\n9. Loading segment_types_seed.tql...")
        if SEGMENT_TYPES_FILE.exists():
            seg_tql = load_tql_file(SEGMENT_TYPES_FILE)
            tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
            try:
                tx.query(seg_tql).resolve()
                tx.commit()
                logger.info(f"   Loaded segment types ({len(seg_tql)} chars)")
            except Exception as e:
                if tx.is_open():
                    tx.close()
                logger.warning(f"   Segment types: {e}")

        # 9b. Load DI reference entities
        logger.info("\n9b. Loading seed_di_reference_entities.tql...")
        if DI_REFERENCE_ENTITIES_FILE.exists():
            di_ref_tql = load_tql_file(DI_REFERENCE_ENTITIES_FILE)
            tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
            try:
                tx.query(di_ref_tql).resolve()
                tx.commit()
                logger.info(f"   Loaded DI reference entities ({len(di_ref_tql)} chars)")
            except Exception as e:
                if tx.is_open():
                    tx.close()
                logger.warning(f"   DI reference entities: {e}")

        # 10. Load new questions
        logger.info("\n10. Loading seed_new_questions.tql...")
        if NEW_QUESTIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, NEW_QUESTIONS_FILE)

        # 11. Load entity-list questions
        logger.info("\n11. Loading seed_entity_list_questions.tql...")
        if ENTITY_LIST_QUESTIONS_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, ENTITY_LIST_QUESTIONS_FILE)

        # 12. Load cross-covenant mappings
        logger.info("\n12. Loading seed_cross_covenant_mappings.tql...")
        if CROSS_COVENANT_MAPPINGS_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, CROSS_COVENANT_MAPPINGS_FILE)

        # 13. Load capacity classifications
        logger.info("\n13. Loading seed_capacity_classifications.tql...")
        if CAPACITY_CLASSIFICATIONS_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, CAPACITY_CLASSIFICATIONS_FILE)

        # 14. Load question annotations (consolidated from 4 files)
        logger.info("\n14. Loading question_annotations.tql...")
        if QUESTION_ANNOTATIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, QUESTION_ANNOTATIONS_FILE)

        # 15. Load MFN entity annotations
        logger.info("\n15. Loading seed_mfn_annotations.tql...")
        if MFN_ANNOTATIONS_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, MFN_ANNOTATIONS_FILE)

        # 16. Load MFN entity-list questions
        logger.info("\n16. Loading seed_mfn_entity_list_questions.tql...")
        if MFN_ENTITY_LIST_QUESTIONS_FILE.exists():
            _load_multi_insert_file(driver, TYPEDB_DATABASE, MFN_ENTITY_LIST_QUESTIONS_FILE)

        # 17. Load synthesis guidance (category-specific analysis rules)
        logger.info("\n17. Loading seed_synthesis_guidance.tql...")
        if SYNTHESIS_GUIDANCE_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, SYNTHESIS_GUIDANCE_FILE)

        # 18. Load annotation functions (SCHEMA transaction)
        logger.info("\n18. Loading annotation_functions.tql...")
        if ANNOTATION_FUNCTIONS_FILE.exists():
            annotation_tql = load_tql_file(ANNOTATION_FUNCTIONS_FILE)
            tx = driver.transaction(TYPEDB_DATABASE, TransactionType.SCHEMA)
            try:
                tx.query(annotation_tql).resolve()
                tx.commit()
                logger.info(f"   Loaded annotation functions ({len(annotation_tql)} chars)")
            except Exception as e:
                if tx.is_open():
                    tx.close()
                logger.warning(f"   Annotation functions: {e}")
                logger.warning("   Annotation functions not available.")

        # 19. Seed storage_value_type on ontology_question (derived from answer_type)
        logger.info("\n19. Seeding storage_value_type on ontology_questions...")
        svt_mappings = {
            "double": ["number", "currency", "percentage"],
            "boolean": ["boolean"],
            "string": ["string", "multiselect"],
            "integer": ["integer"],
        }
        svt_count = 0
        for svt, answer_types in svt_mappings.items():
            for at in answer_types:
                query = f'''match
                    $q isa ontology_question, has answer_type "{at}";
                    not {{ $q has storage_value_type $existing; }};
                insert
                    $q has storage_value_type "{svt}";'''
                tx = driver.transaction(TYPEDB_DATABASE, TransactionType.WRITE)
                try:
                    rows = list(tx.query(query).resolve().as_concept_rows())
                    tx.commit()
                    n = len(rows)
                    if n > 0:
                        logger.info(f"   answer_type={at} -> storage_value_type={svt}: {n} questions")
                        svt_count += n
                except Exception as e:
                    if tx.is_open():
                        tx.close()
                    logger.debug(f"   storage_value_type {at}->{svt}: {e}")
        logger.info(f"   Total: {svt_count} questions got storage_value_type")

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
                ("Questions (incl J.Crew + MFN)", "match $q isa ontology_question; select $q;", 279),
                ("Categories (incl JC1-3, MFN1-6)", "match $cat isa ontology_category; select $cat;", 26),
                ("Extraction metadata (total)", "match $em isa extraction_metadata; select $em;", 0),
                ("IP types", "match $ip isa ip_type; select $ip;", 5),
                ("Segment types", "match $s isa document_segment_type; select $s;", 21),
                ("Attribute annotations", "match $r isa question_annotates_attribute; select $r;", 210),
                ("DI ratio_test_type", "match $r isa ratio_test_type; select $r;", 5),
                ("DI debt_condition_type", "match $c isa debt_condition_type; select $c;", 16),
                ("DI di_lien_priority", "match $p isa di_lien_priority; select $p;", 4),
                ("DI di_facility_type", "match $f isa di_facility_type; select $f;", 10),
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
