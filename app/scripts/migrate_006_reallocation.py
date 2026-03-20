"""
Migration 006: Reallocation graph edges.

Adds: investment_provision type, cross_covenant_mapping type + seed,
      expanded basket_reallocates_to relation.

Run: python -m app.scripts.migrate_006_reallocation
Safe to run multiple times (define is idempotent, insert is idempotent with @key).
"""
import logging
from pathlib import Path
from typedb.driver import TransactionType
from app.services.typedb_client import typedb_client
from app.config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"


def run():
    if not typedb_client.driver:
        typedb_client.connect()

    db = settings.typedb_database

    # ── Phase 1: Schema changes (single idempotent define) ──────────────
    define_tql = (DATA_DIR / "migration_006_reallocation_graph.tql").read_text()

    logger.info(f"Running migration 006 schema changes on '{db}'...")
    tx = typedb_client.driver.transaction(db, TransactionType.SCHEMA)
    try:
        tx.query(define_tql).resolve()
        tx.commit()
        logger.info("Schema migration 006 complete.")
    except Exception as e:
        logger.error(f"Schema migration failed: {e}")
        tx.close()
        raise

    # ── Phase 2: Seed cross-covenant mappings (WRITE tx) ──────────────────
    seed_tql = (DATA_DIR / "seed_cross_covenant_mappings.tql").read_text()
    logger.info("Seeding cross-covenant mappings...")
    tx = typedb_client.driver.transaction(db, TransactionType.WRITE)
    try:
        # Check if already seeded
        check = list(tx.query(
            'match $m isa cross_covenant_mapping; select $m;'
        ).resolve().as_concept_rows())
        if check:
            logger.info(f"  Already seeded ({len(check)} mappings). Skipping.")
            tx.close()
        else:
            tx.query(seed_tql).resolve()
            tx.commit()
            logger.info("  Seeded cross-covenant mappings.")
    except Exception as e:
        logger.error(f"Seed failed: {e}")
        tx.close()
        raise

    # ── Phase 3: Update rp_el_reallocations extraction_prompt ─────────────
    update_extraction_prompt(db)


def update_extraction_prompt(db: str):
    """Update the extraction prompt for rp_el_reallocations on the live database."""
    new_prompt = (
        'Identify ALL reallocation paths that move capacity between baskets.\n\n'
        'For EACH reallocation path, model EACH DIRECTION as a SEPARATE entity.\n'
        'If capacity flows from RDP basket -> RP basket AND from RP basket -> RDP basket,\n'
        'that is TWO entities with different source/target, not one with is_bidirectional=true.\n\n'
        'In ADDITION to the schema fields listed above, include these routing fields on each entity:\n'
        '- source_basket_type: TypeDB entity type of the SOURCE basket. '
        'Must be one of: {basket_subtypes}\n'
        '- target_basket_type: TypeDB entity type of the TARGET basket. '
        'Must be one of: {basket_subtypes}\n\n'
        'MAPPING GUIDE for source_basket_type / target_basket_type:\n'
        "- 'Section 6.03(y) Investment basket' -> general_investment_basket\n"
        "- 'Section 6.09(a) RDP basket' -> general_rdp_basket\n"
        "- 'Section 6.06(j) general RP basket' -> general_rp_basket\n"
        "- 'Available Amount / Cumulative Amount' -> builder_basket\n"
        "- 'Ratio RP basket' -> ratio_basket\n\n"
        'You MUST also populate reallocation_source as a human-readable description '
        "(e.g. 'Section 6.03(y) Investment basket').\n\n"
        'This is critical for total dividend capacity calculation.'
    )

    tx = typedb_client.driver.transaction(db, TransactionType.WRITE)
    try:
        # Delete old prompt
        tx.query('''
            match
                $q isa ontology_question, has question_id "rp_el_reallocations",
                    has extraction_prompt $old;
            delete
                $old of $q;
        ''').resolve()

        # Insert new
        escaped = new_prompt.replace('\\', '\\\\').replace('"', '\\"')
        tx.query(f'''
            match
                $q isa ontology_question, has question_id "rp_el_reallocations";
            insert
                $q has extraction_prompt "{escaped}";
        ''').resolve()

        tx.commit()
        logger.info("Updated rp_el_reallocations extraction_prompt.")
    except Exception as e:
        logger.error(f"Failed to update extraction_prompt: {e}")
        tx.close()
        raise


if __name__ == "__main__":
    run()
