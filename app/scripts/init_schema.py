"""
Initialize TypeDB Schema for ValenceV3

Run this once after setting up TypeDB Cloud:
    python -m app.scripts.init_schema

This creates the database (if needed), loads schema, and seeds data.
"""
import os
import sys
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from typedb.driver import TypeDB, SessionType, TransactionType

# Configuration from environment
TYPEDB_ADDRESS = os.getenv("TYPEDB_ADDRESS", "localhost:1729")
TYPEDB_DATABASE = os.getenv("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.getenv("TYPEDB_USERNAME", "")
TYPEDB_PASSWORD = os.getenv("TYPEDB_PASSWORD", "")

# Data files
DATA_DIR = Path(__file__).parent.parent / "data"
SCHEMA_FILE = DATA_DIR / "schema_unified.tql"
CONCEPTS_FILE = DATA_DIR / "concepts.tql"
QUESTIONS_FILE = DATA_DIR / "questions.tql"
CATEGORY_M_FILE = DATA_DIR / "ontology_category_m.tql"


def get_driver():
    """Get TypeDB driver."""
    # Normalize address for TypeDB 3.x
    address = TYPEDB_ADDRESS
    
    # Strip any http/https prefix
    for prefix in ["https://", "http://"]:
        if address.startswith(prefix):
            address = address[len(prefix):]
    
    if TYPEDB_USERNAME and TYPEDB_PASSWORD:
        # TypeDB Cloud
        from typedb.driver import Credential
        # Add https:// back for cloud
        cloud_address = f"https://{address}" if not address.startswith("https://") else address
        credential = Credential(TYPEDB_USERNAME, TYPEDB_PASSWORD, True)
        logger.info(f"Connecting to TypeDB Cloud: {cloud_address}")
        return TypeDB.cloud_driver(cloud_address, credential)
    else:
        # TypeDB Core (local)
        logger.info(f"Connecting to TypeDB Core: {address}")
        return TypeDB.core_driver(address)


def load_tql_file(filepath: Path) -> str:
    """Load and clean TQL file."""
    content = filepath.read_text()
    # Remove comment lines
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
    content = filepath.read_text()
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
                match_insert_statements.append('\n'.join(current_statement))
                current_statement = []
            in_insert_block = True
            in_match_block = False
            insert_lines.append(stripped)
            continue

        if stripped.startswith('match '):
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
        with driver.session(db_name, SessionType.DATA) as session:
            with session.transaction(TransactionType.WRITE) as tx:
                try:
                    tx.query(insert_tql)
                    tx.commit()
                    logger.info(f"  ✓ Executed insert block ({len(insert_lines)} lines)")
                except Exception as e:
                    if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                        logger.info("  ✓ Insert block already exists (skipping)")
                    else:
                        logger.warning(f"  Insert block error: {e}")

    # Execute match-insert statements
    for stmt in match_insert_statements:
        with driver.session(db_name, SessionType.DATA) as session:
            with session.transaction(TransactionType.WRITE) as tx:
                try:
                    tx.query(stmt)
                    tx.commit()
                except Exception as e:
                    if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                        pass  # Silent skip for duplicates
                    else:
                        logger.warning(f"  Match-insert error: {e}")

    logger.info(f"  ✓ Executed {len(match_insert_statements)} match-insert statements")


def init_database():
    """Initialize TypeDB with ValenceV3 schema."""
    logger.info("=" * 60)
    logger.info("ValenceV3 Schema Initialization")
    logger.info("=" * 60)
    
    driver = get_driver()
    
    try:
        # Check if database exists
        databases = [db.name for db in driver.databases.all()]
        logger.info(f"Existing databases: {databases}")
        
        if TYPEDB_DATABASE in databases:
            logger.warning(f"Database '{TYPEDB_DATABASE}' already exists.")
            response = input("Drop and recreate? (yes/no): ").strip().lower()
            if response != 'yes':
                logger.info("Aborted. Use existing database.")
                return
            driver.databases.get(TYPEDB_DATABASE).delete()
            logger.info(f"✓ Dropped database '{TYPEDB_DATABASE}'")
        
        # Create database
        driver.databases.create(TYPEDB_DATABASE)
        logger.info(f"✓ Created database '{TYPEDB_DATABASE}'")
        
        # Load schema
        logger.info("\nLoading schema...")
        if not SCHEMA_FILE.exists():
            logger.error(f"✗ Schema file not found: {SCHEMA_FILE}")
            return
        
        schema_tql = load_tql_file(SCHEMA_FILE)
        with driver.session(TYPEDB_DATABASE, SessionType.SCHEMA) as session:
            with session.transaction(TransactionType.WRITE) as tx:
                tx.query(schema_tql)
                tx.commit()
        logger.info(f"✓ Loaded schema ({len(schema_tql)} chars)")
        
        # Load concept seed data
        logger.info("\nLoading concept seed data...")
        if CONCEPTS_FILE.exists():
            concepts_tql = load_tql_file(CONCEPTS_FILE)
            with driver.session(TYPEDB_DATABASE, SessionType.DATA) as session:
                with session.transaction(TransactionType.WRITE) as tx:
                    tx.query(concepts_tql)
                    tx.commit()
            logger.info(f"✓ Loaded concepts ({len(concepts_tql)} chars)")
        else:
            logger.warning(f"⚠ Concepts file not found: {CONCEPTS_FILE}")
        
        # Load question seed data
        logger.info("\nLoading question seed data...")
        if QUESTIONS_FILE.exists():
            questions_tql = load_tql_file(QUESTIONS_FILE)
            with driver.session(TYPEDB_DATABASE, SessionType.DATA) as session:
                with session.transaction(TransactionType.WRITE) as tx:
                    tx.query(questions_tql)
                    tx.commit()
            logger.info(f"✓ Loaded questions ({len(questions_tql)} chars)")
        else:
            logger.warning(f"⚠ Questions file not found: {QUESTIONS_FILE}")

        # Load Category M ontology (Unrestricted Subsidiary Distributions)
        logger.info("\nLoading Category M ontology...")
        if CATEGORY_M_FILE.exists():
            _load_mixed_tql_file(driver, TYPEDB_DATABASE, CATEGORY_M_FILE)
            logger.info("✓ Loaded Category M ontology")
        else:
            logger.warning(f"⚠ Category M file not found: {CATEGORY_M_FILE}")

        # Verify
        logger.info("\n" + "=" * 60)
        logger.info("Verification")
        logger.info("=" * 60)
        
        with driver.session(TYPEDB_DATABASE, SessionType.DATA) as session:
            with session.transaction(TransactionType.READ) as tx:
                # Count concepts
                result = list(tx.query("match $c isa concept; select $c;").as_concept_rows())
                logger.info(f"  Concepts loaded: {len(result)}")
                
                # Count questions  
                result = list(tx.query("match $q isa ontology_question; select $q;").as_concept_rows())
                logger.info(f"  Questions loaded: {len(result)}")
                
                # Count categories
                result = list(tx.query("match $cat isa ontology_category; select $cat;").as_concept_rows())
                logger.info(f"  Categories loaded: {len(result)}")
        
        logger.info("\n" + "=" * 60)
        logger.info("✓ ValenceV3 schema initialization complete!")
        logger.info("=" * 60)
        
    except Exception as e:
        logger.error(f"✗ Initialization failed: {e}")
        raise
    finally:
        driver.close()


if __name__ == "__main__":
    init_database()
