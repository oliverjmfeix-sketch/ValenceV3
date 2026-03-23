"""Verify MFN entities were created with populated attributes."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from typedb.driver import TypeDB, Credentials, DriverOptions, TransactionType

TYPEDB_ADDRESS = os.environ.get("TYPEDB_ADDRESS", "ip654h-0.cluster.typedb.com:80")
TYPEDB_DATABASE = os.environ.get("TYPEDB_DATABASE", "valence")
TYPEDB_USERNAME = os.environ.get("TYPEDB_USERNAME", "admin")
TYPEDB_PASSWORD = os.environ.get("TYPEDB_PASSWORD", "")
DEAL_ID = sys.argv[1] if len(sys.argv) > 1 else "8d0bf2f8"
PROVISION_ID = f"{DEAL_ID}_mfn"


def main():
    address = TYPEDB_ADDRESS
    if not address.startswith("http://") and not address.startswith("https://"):
        address = f"https://{address}"
    driver = TypeDB.driver(address, Credentials(TYPEDB_USERNAME, TYPEDB_PASSWORD), DriverOptions())
    tx = driver.transaction(TYPEDB_DATABASE, TransactionType.READ)

    print("=" * 60)
    print(f"MFN Entity Verification — deal {DEAL_ID}")
    print("=" * 60)

    entity_types = ["mfn_exclusion", "mfn_yield_definition", "mfn_sunset_provision", "mfn_freebie_basket"]

    for etype in entity_types:
        query = f"""
            match
                $p isa mfn_provision, has provision_id "{PROVISION_ID}";
                (provision: $p, extracted: $e) isa $rel;
                $e isa {etype};
            fetch {{
                "attrs": {{ $e.* }}
            }};
        """
        try:
            docs = list(tx.query(query).resolve().as_concept_documents())
            print(f"\n{etype}: {len(docs)} entities")
            for i, doc in enumerate(docs[:3]):
                attrs = doc.get("attrs", {})
                # Count non-provenance attributes
                prov_keys = {"source_text", "source_page", "section_reference", "confidence"}
                domain_attrs = {k: v for k, v in attrs.items() if k not in prov_keys and not k.endswith("_id")}
                print(f"  [{i}] {len(domain_attrs)} domain attrs: {list(domain_attrs.keys())[:6]}")
                for k, v in list(domain_attrs.items())[:3]:
                    val_str = str(v)[:80]
                    print(f"      {k}: {val_str}")
            if len(docs) > 3:
                print(f"  ... and {len(docs) - 3} more")
        except Exception as e:
            print(f"\n{etype}: ERROR — {e}")

    tx.close()
    print("\n" + "=" * 60)
    print("Verification complete")
    print("=" * 60)


if __name__ == "__main__":
    main()
