"""Cross-covenant relation service for DI↔MFN and DI↔RP linking.

SSoT: All trigger data read from TypeDB provision_has_answer, not Python dicts.
Called after all individual covenant extractions complete (RP → DI → MFN).
"""

import logging
from app.services.typedb_client import typedb_client
from app.config import settings
from typedb.driver import TransactionType

logger = logging.getLogger(__name__)


class CrossCovenantService:
    """Service for creating cross-covenant relations in TypeDB."""

    async def link_di_to_mfn(self, deal_id: str) -> bool:
        """Create di_provision_links_mfn relation if both provisions exist."""
        di_pid = f"{deal_id}_di"
        mfn_pid = f"{deal_id}_mfn"

        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            result = tx.query(f'''
                match
                    $di isa di_provision, has provision_id "{di_pid}";
                    $mfn isa mfn_provision, has provision_id "{mfn_pid}";
                    not {{ (di_prov: $di, mfn_prov: $mfn) isa di_provision_links_mfn; }};
                insert
                    (di_prov: $di, mfn_prov: $mfn) isa di_provision_links_mfn;
            ''').resolve()
            rows = list(result.as_concept_rows())
            tx.commit()
            if rows:
                logger.info(f"Created di_provision_links_mfn for {deal_id}")
            else:
                logger.info(f"di_provision_links_mfn already exists or provisions missing for {deal_id}")
            return bool(rows)
        except Exception as e:
            logger.warning(f"Could not link DI↔MFN for {deal_id}: {e}")
            if tx.is_open():
                tx.close()
            return False

    async def link_di_to_rp(self, deal_id: str) -> bool:
        """Create di_provision_links_rp relation if both provisions exist."""
        di_pid = f"{deal_id}_di"
        rp_pid = f"{deal_id}_rp"

        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            result = tx.query(f'''
                match
                    $di isa di_provision, has provision_id "{di_pid}";
                    $rp isa rp_provision, has provision_id "{rp_pid}";
                    not {{ (di_prov: $di, rp_prov: $rp) isa di_provision_links_rp; }};
                insert
                    (di_prov: $di, rp_prov: $rp) isa di_provision_links_rp;
            ''').resolve()
            rows = list(result.as_concept_rows())
            tx.commit()
            if rows:
                logger.info(f"Created di_provision_links_rp for {deal_id}")
            else:
                logger.info(f"di_provision_links_rp already exists or provisions missing for {deal_id}")
            return bool(rows)
        except Exception as e:
            logger.warning(f"Could not link DI↔RP for {deal_id}: {e}")
            if tx.is_open():
                tx.close()
            return False

    async def populate_incremental_mfn_triggers(self, deal_id: str) -> bool:
        """Create incremental_triggers_mfn relation with attributes from TypeDB.

        SSoT: Reads trigger values from incremental_facility entity attributes
        stored during DI extraction, not from Python extraction_result dict.
        """
        di_pid = f"{deal_id}_di"
        mfn_pid = f"{deal_id}_mfn"

        # Read trigger values from incremental_facility attributes (SSoT)
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            result = tx.query(f'''
                match
                    $di isa di_provision, has provision_id "{di_pid}";
                    (provision: $di, incremental: $incr) isa provision_has_incremental;
                    try {{ $incr has ied_triggers_mfn $ied_mfn; }};
                    try {{ $incr has mfn_excludes_freebie $excl_free; }};
                    try {{ $incr has mfn_sunset_months $sunset; }};
                    try {{ $incr has ied_permitted $ied_perm; }};
                select $ied_mfn, $excl_free, $sunset, $ied_perm;
            ''').resolve()

            rows = list(result.as_concept_rows())
            if not rows:
                logger.info(f"No incremental_facility found for {deal_id}")
                return False

            row = rows[0]
            # Derive trigger attributes from incremental_facility data
            ied_triggers = row.get("ied_mfn")
            triggers_for_ied = ied_triggers.as_attribute().get_value() if ied_triggers else False

            excl_free = row.get("excl_free")
            freebie_exempt = not excl_free.as_attribute().get_value() if excl_free else False

            sunset_concept = row.get("sunset")
            sunset_months = sunset_concept.as_attribute().get_value() if sunset_concept else 0

            ied_perm = row.get("ied_perm")
            ied_permitted = ied_perm.as_attribute().get_value() if ied_perm else False
        except Exception as e:
            logger.warning(f"Could not read incremental_facility trigger data for {deal_id}: {e}")
            return False
        finally:
            tx.close()

        # Create the relation with derived attributes
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            # Term loans always trigger MFN; revolvers typically don't
            triggers_term = "true"
            triggers_rev = "false"
            triggers_ied_str = str(triggers_for_ied).lower()
            freebie_usd = 0.0  # Default; could read from mfn_freebie_basket entity if needed

            result = tx.query(f'''
                match
                    $di isa di_provision, has provision_id "{di_pid}";
                    (provision: $di, incremental: $incr) isa provision_has_incremental;
                    $mfn isa mfn_provision, has provision_id "{mfn_pid}";
                    not {{ (incremental: $incr, mfn_prov: $mfn) isa incremental_triggers_mfn; }};
                insert
                    (incremental: $incr, mfn_prov: $mfn) isa incremental_triggers_mfn,
                        has triggers_for_term_loans {triggers_term},
                        has triggers_for_revolvers {triggers_rev},
                        has triggers_for_ied {triggers_ied_str},
                        has freebie_exempt_usd {freebie_usd},
                        has mfn_sunset_months {sunset_months};
            ''').resolve()
            rows = list(result.as_concept_rows())
            tx.commit()
            if rows:
                logger.info(f"Created incremental_triggers_mfn for {deal_id} "
                            f"(ied_triggers={triggers_for_ied}, sunset={sunset_months}mo)")
            return bool(rows)
        except Exception as e:
            logger.warning(f"Could not create incremental_triggers_mfn for {deal_id}: {e}")
            if tx.is_open():
                tx.close()
            return False

    async def link_contribution_to_builder(self, deal_id: str) -> bool:
        """Create di_feeds_rp_builder relation between contribution basket and builder basket.

        SSoT: Reads is_dollar_for_dollar from contribution_basket entity attribute.
        """
        di_pid = f"{deal_id}_di"
        rp_pid = f"{deal_id}_rp"

        # Read is_dollar_for_dollar from DI provision answers
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            result = tx.query(f'''
                match
                    $di isa di_provision, has provision_id "{di_pid}",
                        has contribution_rp_dollar_for_dollar $d4d;
                select $d4d;
            ''').resolve()
            rows = list(result.as_concept_rows())
            is_d4d = rows[0].get("d4d").as_attribute().get_value() if rows else True
        except Exception as e:
            logger.debug(f"Could not read contribution_rp_dollar_for_dollar: {e}")
            is_d4d = True  # Default assumption
        finally:
            tx.close()

        # Create the relation
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
        try:
            result = tx.query(f'''
                match
                    $di isa di_provision, has provision_id "{di_pid}";
                    (provision: $di, di_basket: $cb) isa provision_has_di_basket;
                    $cb isa contribution_basket;
                    $rp isa rp_provision, has provision_id "{rp_pid}";
                    (provision: $rp, basket: $bb) isa provision_has_basket;
                    $bb isa builder_basket;
                    not {{ (contrib: $cb, builder: $bb) isa di_feeds_rp_builder; }};
                insert
                    (contrib: $cb, builder: $bb) isa di_feeds_rp_builder,
                        has is_dollar_for_dollar {str(is_d4d).lower()};
            ''').resolve()
            rows = list(result.as_concept_rows())
            tx.commit()
            if rows:
                logger.info(f"Created di_feeds_rp_builder for {deal_id} (d4d={is_d4d})")
            return bool(rows)
        except Exception as e:
            logger.warning(f"Could not create di_feeds_rp_builder for {deal_id}: {e}")
            if tx.is_open():
                tx.close()
            return False

    async def link_all_covenants(self, deal_id: str) -> dict:
        """Run all cross-covenant linking for a deal after extraction.

        No extraction_result dict needed — reads all data from TypeDB (SSoT).
        """
        results = {
            "di_mfn_linked": False,
            "di_rp_linked": False,
            "incremental_mfn_triggers": False,
            "contribution_builder": False,
        }

        results["di_mfn_linked"] = await self.link_di_to_mfn(deal_id)
        results["di_rp_linked"] = await self.link_di_to_rp(deal_id)

        if results["di_mfn_linked"]:
            results["incremental_mfn_triggers"] = await self.populate_incremental_mfn_triggers(deal_id)

        if results["di_rp_linked"]:
            results["contribution_builder"] = await self.link_contribution_to_builder(deal_id)

        logger.info(f"Cross-covenant linking for {deal_id}: {results}")
        return results


cross_covenant_service = CrossCovenantService()
