"""
Valence V3 Backend - Covenant Intelligence Platform

FastAPI application with:
- TypeDB 3.x connection on startup
- Auto-schema initialization if database is empty
- CORS configuration for Lovable frontend
- All API routers mounted
"""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from typedb.driver import TransactionType

from app.config import settings
from app.services.typedb_client import typedb_client
from app.routers import health, deals, ontology, qa, patterns

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def _load_tql_file(filepath: Path) -> str:
    """Load and clean TQL file - remove comments."""
    if not filepath.exists():
        logger.warning(f"TQL file not found: {filepath}")
        return ""
    content = filepath.read_text()
    lines = [l for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]
    return '\n'.join(lines)


def _load_categories_with_relations(driver, db_name: str, filepath: Path):
    """
    Load categories.tql which contains both insert and match-insert statements.

    The file has:
    1. A bulk 'insert' block for category entities
    2. Individual 'match ... insert' statements for category_has_question relations

    These must be executed separately because TypeQL can't mix them in one query.
    """
    if not filepath.exists():
        logger.warning(f"Categories file not found: {filepath}")
        return

    content = filepath.read_text()

    # Split into insert block and match-insert statements
    # The insert block ends when we hit the first 'match' keyword
    lines = content.split('\n')
    insert_lines = []
    match_insert_statements = []
    current_statement = []
    in_insert_block = False
    in_match_block = False

    for line in lines:
        stripped = line.strip()

        # Skip comments and empty lines
        if not stripped or stripped.startswith('#'):
            continue

        # Detect start of insert block
        if stripped == 'insert':
            in_insert_block = True
            insert_lines.append(stripped)
            continue

        # Detect start of match-insert statement
        if stripped.startswith('match '):
            in_insert_block = False
            in_match_block = True
            if current_statement:
                match_insert_statements.append('\n'.join(current_statement))
            current_statement = [stripped]
            continue

        # Add to current context
        if in_insert_block:
            insert_lines.append(stripped)
        elif in_match_block:
            current_statement.append(stripped)

    # Don't forget the last statement
    if current_statement:
        match_insert_statements.append('\n'.join(current_statement))

    # 1. Execute insert block for categories
    if insert_lines:
        insert_tql = '\n'.join(insert_lines)
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(insert_tql).resolve()
            tx.commit()
            logger.info(f"✓ Category entities loaded")
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "unique" in error_msg or "already" in error_msg:
                logger.info("✓ Category entities already exist")
            else:
                logger.warning(f"Category insert error: {e}")

    # 2. Execute each match-insert statement for relations
    relations_created = 0
    relations_skipped = 0

    for stmt in match_insert_statements:
        if not stmt.strip():
            continue
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            relations_created += 1
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "unique" in error_msg or "already" in error_msg or "duplicate" in error_msg:
                relations_skipped += 1
            else:
                logger.debug(f"Relation insert error: {e}")
                relations_skipped += 1

    logger.info(f"✓ Category relations: {relations_created} created, {relations_skipped} skipped")


def _load_ontology_expanded(driver, db_name: str, filepath: Path):
    """
    Load ontology_expanded.tql which contains:
    1. Insert statements for categories, questions, concepts, target fields
    2. Match-insert statements for category_has_question and question_targets_* relations

    These must be executed separately because TypeQL can't mix them in one query.
    """
    if not filepath.exists():
        logger.warning(f"Ontology expanded file not found: {filepath}")
        return

    content = filepath.read_text()

    # Remove comment lines
    lines = [l for l in content.split('\n') if l.strip() and not l.strip().startswith('#')]
    clean_content = '\n'.join(lines)

    # Split by semicolons to get individual statements
    raw_statements = [s.strip() for s in clean_content.split(';') if s.strip()]

    insert_statements = []
    match_insert_statements = []

    for stmt in raw_statements:
        if stmt.startswith('match ') or stmt.startswith('match\n'):
            match_insert_statements.append(stmt + ';')
        elif stmt.startswith('insert ') or stmt.startswith('insert\n'):
            insert_statements.append(stmt + ';')

    logger.info(f"Parsed {len(insert_statements)} inserts, {len(match_insert_statements)} match-inserts from ontology_expanded.tql")

    # Log first insert statement for debugging
    if insert_statements:
        logger.info(f"First insert statement: {insert_statements[0][:150]}...")

    # 1. Execute insert statements (categories, questions, concepts, target fields)
    inserts_created = 0
    inserts_skipped = 0
    inserts_failed = 0

    for stmt in insert_statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            inserts_created += 1
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "unique" in error_msg or "already" in error_msg or "duplicate" in error_msg:
                inserts_skipped += 1
            else:
                inserts_failed += 1
                # Log more failures for debugging
                if inserts_failed <= 10:
                    logger.warning(f"Insert failed ({inserts_failed}): {e}")
                    logger.warning(f"Statement: {stmt[:300]}...")

    logger.info(f"✓ Ontology expanded inserts: {inserts_created} created, {inserts_skipped} skipped, {inserts_failed} failed")

    # 2. Execute match-insert statements (relations)
    relations_created = 0
    relations_skipped = 0
    relations_failed = 0

    for stmt in match_insert_statements:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            tx.query(stmt).resolve()
            tx.commit()
            relations_created += 1
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "unique" in error_msg or "already" in error_msg or "duplicate" in error_msg:
                relations_skipped += 1
            else:
                relations_failed += 1
                if relations_failed <= 3:
                    logger.warning(f"Relation insert failed: {e}")

    logger.info(f"✓ Ontology expanded relations: {relations_created} created, {relations_skipped} skipped, {relations_failed} failed")


def _try_incremental_schema_update(driver, db_name: str, schema_tql: str):
    """
    Try to add new schema elements incrementally.

    Parses the schema and attempts to define each entity/attribute/relation
    type individually, skipping ones that already exist.
    """
    import re

    # Extract individual type definitions
    # Match patterns like: entity foo sub bar, ... ;  or  attribute foo, value string;
    lines = schema_tql.split('\n')
    current_def = []
    definitions = []
    in_define = False

    for line in lines:
        stripped = line.strip()

        # Skip define keyword
        if stripped == 'define':
            in_define = True
            continue

        if not in_define or not stripped:
            continue

        current_def.append(line)

        # Check if this line ends a definition (ends with ;)
        if stripped.endswith(';'):
            definitions.append('\n'.join(current_def))
            current_def = []

    logger.info(f"Attempting incremental schema update with {len(definitions)} definitions")

    # Try each definition
    added = 0
    skipped = 0
    failed = 0

    for defn in definitions:
        # Skip function definitions (fun keyword) - they need special handling
        if defn.strip().startswith('fun '):
            continue

        tx = driver.transaction(db_name, TransactionType.SCHEMA)
        try:
            tx.query(f"define\n{defn}")
            tx.commit()
            added += 1
            logger.info(f"Added schema: {defn[:50]}...")
        except Exception as e:
            tx.close()
            error_msg = str(e).lower()
            if "already" in error_msg or "exists" in error_msg or "duplicate" in error_msg:
                skipped += 1
            else:
                failed += 1
                # Log all errors for debugging
                logger.warning(f"Schema element failed: {defn[:80]}... - Error: {str(e)[:100]}")

    logger.info(f"✓ Schema update: {added} added, {skipped} already existed, {failed} failed")


async def _ensure_schema_loaded():
    """
    Auto-initialize schema if database is empty.
    
    Uses TypeDB 3.x API directly via the driver.
    Handles "already exists" errors gracefully.
    """
    driver = typedb_client.driver
    db_name = settings.typedb_database
    
    if not driver:
        logger.error("No TypeDB driver available")
        return
    
    # Check if database exists
    if not driver.databases.contains(db_name):
        logger.info(f"Creating database: {db_name}")
        driver.databases.create(db_name)
    
    DATA_DIR = Path(__file__).parent / "data"
    
    # 1. Load schema (define statements)
    # TypeDB define is idempotent for new types but may error on existing ones
    # We try the full schema first, then fall back to individual statements if needed
    schema_file = DATA_DIR / "schema.tql"
    if schema_file.exists():
        logger.info("Loading schema.tql...")
        schema_tql = _load_tql_file(schema_file)
        if schema_tql:
            tx = driver.transaction(db_name, TransactionType.SCHEMA)
            try:
                tx.query(schema_tql)
                tx.commit()
                logger.info("✓ Schema loaded")
            except Exception as e:
                tx.close()
                error_msg = str(e).lower()
                # Schema partially exists - try to extend it with new types
                if "already" in error_msg or "duplicate" in error_msg or "exists" in error_msg:
                    logger.info("Schema exists, attempting incremental update...")
                    # Extract and try each entity/attribute/relation definition separately
                    _try_incremental_schema_update(driver, db_name, schema_tql)
                else:
                    logger.error(f"Schema load failed: {e}")
                    return
    else:
        logger.error(f"Schema file not found: {schema_file}")
        return

    # 1b. Load schema updates (v2 - qualifications, cross-references, citations)
    schema_v2_file = DATA_DIR / "schema_v2.tql"
    if schema_v2_file.exists():
        logger.info("Loading schema_v2.tql...")
        schema_v2_tql = _load_tql_file(schema_v2_file)
        if schema_v2_tql:
            tx = driver.transaction(db_name, TransactionType.SCHEMA)
            try:
                tx.query(schema_v2_tql).resolve()
                tx.commit()
                logger.info("✓ Schema v2 loaded")
            except Exception as e:
                tx.close()
                error_msg = str(e).lower()
                if "already" in error_msg or "duplicate" in error_msg or "exists" in error_msg:
                    logger.info("✓ Schema v2 already exists (skipping)")
                else:
                    logger.warning(f"Schema v2 load: {e}")

    # 1c. Load expanded schema (new concept types, new rp_provision attributes)
    schema_expanded_file = DATA_DIR / "schema_expanded.tql"
    if schema_expanded_file.exists():
        logger.info("Loading schema_expanded.tql...")
        schema_expanded_tql = _load_tql_file(schema_expanded_file)
        if schema_expanded_tql:
            tx = driver.transaction(db_name, TransactionType.SCHEMA)
            try:
                tx.query(schema_expanded_tql).resolve()
                tx.commit()
                logger.info("✓ Schema expanded loaded")
            except Exception as e:
                tx.close()
                error_msg = str(e).lower()
                if "already" in error_msg or "duplicate" in error_msg or "exists" in error_msg:
                    logger.info("Schema expanded partially exists, trying incremental update...")
                    _try_incremental_schema_update(driver, db_name, schema_expanded_tql)
                else:
                    logger.warning(f"Schema expanded load error: {e}")
                    # Try incremental update even on other errors
                    logger.info("Attempting incremental schema update...")
                    _try_incremental_schema_update(driver, db_name, schema_expanded_tql)

    # 2. Load concepts (insert statements) - skip if already loaded
    concepts_file = DATA_DIR / "concepts.tql"
    if concepts_file.exists():
        logger.info("Loading concepts.tql...")
        concepts_tql = _load_tql_file(concepts_file)
        if concepts_tql:
            tx = driver.transaction(db_name, TransactionType.WRITE)
            try:
                tx.query(concepts_tql).resolve()
                tx.commit()
                logger.info("✓ Concepts loaded")
            except Exception as e:
                tx.close()
                error_msg = str(e).lower()
                if "already exists" in error_msg or "duplicate" in error_msg:
                    logger.info("✓ Concepts already exist (skipping)")
                else:
                    logger.warning(f"Concepts load: {e}")
    
    # 3. Load questions (insert statements) - skip if already loaded
    questions_file = DATA_DIR / "questions.tql"
    if questions_file.exists():
        logger.info("Loading questions.tql...")
        questions_tql = _load_tql_file(questions_file)
        if questions_tql:
            tx = driver.transaction(db_name, TransactionType.WRITE)
            try:
                tx.query(questions_tql).resolve()
                tx.commit()
                logger.info("✓ Questions loaded")
            except Exception as e:
                tx.close()
                error_msg = str(e).lower()
                if "already exists" in error_msg or "duplicate" in error_msg:
                    logger.info("✓ Questions already exist (skipping)")
                else:
                    logger.warning(f"Questions load: {e}")

    # 4. Load categories and relations (requires special handling for match-insert)
    categories_file = DATA_DIR / "categories.tql"
    if categories_file.exists():
        logger.info("Loading categories.tql...")
        _load_categories_with_relations(driver, db_name, categories_file)

    # 5. Load expanded ontology (36 new questions + concepts + relations)
    ontology_expanded_file = DATA_DIR / "ontology_expanded.tql"
    if ontology_expanded_file.exists():
        logger.info("Loading ontology_expanded.tql...")
        _load_ontology_expanded(driver, db_name, ontology_expanded_file)

    logger.info("✓ Schema initialization complete!")


def _cleanup_old_sample_questions():
    """
    Remove old sample questions (rp_q*, mfn_q*) that have been replaced
    by the consolidated ontology questions.
    """
    driver = typedb_client.driver
    db_name = settings.typedb_database

    if not driver:
        logger.warning("No driver available for cleanup")
        return

    logger.info("Checking for old sample questions to clean up...")

    # Old question IDs to delete (replaced by consolidated questions)
    old_question_ids = [
        "rp_q1", "rp_q30", "rp_q51", "rp_q52", "rp_q87", "rp_q88", "rp_q89",
        "rp_q250", "rp_q260", "rp_q270"
    ]

    deleted = 0
    for qid in old_question_ids:
        tx = driver.transaction(db_name, TransactionType.WRITE)
        try:
            # First check if question exists
            check_query = f"""
                match $q isa ontology_question, has question_id "{qid}";
                select $q;
            """
            check_result = tx.query(check_query).resolve()
            rows = list(check_result.as_concept_rows())

            if rows:
                # Delete the question entity (TypeDB 3.x syntax)
                delete_query = f"""
                    match $q isa ontology_question, has question_id "{qid}";
                    delete $q;
                """
                tx.query(delete_query).resolve()
                tx.commit()
                deleted += 1
                logger.debug(f"Deleted old question: {qid}")
            else:
                tx.close()
        except Exception as e:
            tx.close()
            logger.warning(f"Could not delete {qid}: {e}")

    logger.info(f"✓ Cleanup complete: {deleted} old sample questions removed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("=" * 60)
    logger.info("Starting Valence V3 Backend...")
    logger.info("=" * 60)
    logger.info(f"TypeDB: {settings.typedb_address}/{settings.typedb_database}")
    logger.info(f"CORS origins: {settings.cors_origins}")
    
    # Connect to TypeDB
    try:
        typedb_client.connect()
        logger.info("✓ TypeDB connected")

        # Auto-initialize schema if database is empty
        await _ensure_schema_loaded()

        # Clean up old sample questions replaced by consolidated ontology
        _cleanup_old_sample_questions()
        
    except Exception as e:
        logger.error(f"✗ Startup error: {e}")
        # Don't fail startup - allow health endpoint to report status
    
    yield
    
    # Shutdown
    logger.info("Shutting down Valence V3...")
    typedb_client.close()
    logger.info("✓ Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title="Valence V3 API",
    description="Covenant Intelligence Platform - Typed Primitives + Provenance",
    version="3.0.0",
    lifespan=lifespan
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(health.router)
app.include_router(deals.router)
app.include_router(ontology.router)
app.include_router(qa.router)
app.include_router(patterns.router)


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "Valence V3 API",
        "version": "3.0.0",
        "description": "Covenant Intelligence with Typed Primitives + Provenance",
        "docs": "/docs",
        "health": "/api/health"
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
