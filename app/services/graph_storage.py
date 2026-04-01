"""
Graph Storage Service - V4 Graph-Native Schema

Handles inserting extracted covenant data as entities and relations
instead of flat attributes.
"""
import json
import logging
import re
import uuid
from typing import Dict, Any, List, Optional
from typedb.driver import TransactionType

from app.services.typedb_client import typedb_client
from app.config import settings
from app.schemas.extraction_response import Answer, ExtractionResponse

logger = logging.getLogger(__name__)

class GraphStorage:
    """Insert extracted covenant data as graph entities and relations."""

    def __init__(self, deal_id: str):
        self.deal_id = deal_id
        self.driver = typedb_client.driver
        self.db_name = settings.typedb_database

    # ═══════════════════════════════════════════════════════════════════════════
    # SCHEMA INTROSPECTION — discover entity fields from TypeDB schema
    # ═══════════════════════════════════════════════════════════════════════════

    _provenance_attrs_cache: Optional[set] = None

    _entity_fields_cache: Dict[str, Any] = {}
    _key_attr_cache: Dict[str, str] = {}

    # Class-level caches for seed/schema data (doesn't change per extraction)
    _q_to_entity_cache: Optional[Dict[str, tuple]] = None
    _concept_routing_cache: Optional[Dict[str, List]] = None
    _entity_list_types_cache: Optional[set] = None
    _entity_relation_cache: Optional[Dict[str, tuple]] = None
    _storage_value_type_cache: Optional[Dict[str, str]] = None
    _cross_covenant_cache: Optional[Dict[str, str]] = None
    _capacity_class_cache: Optional[Dict[str, str]] = None
    _relation_config_cache: Optional[Dict[str, Dict]] = None

    @classmethod
    def _load_provenance_attrs(cls) -> set:
        """Discover provenance attributes: those owned by ALL extracted entity types.

        Provenance = framework infrastructure (WHERE data came from).
        Domain = entity-specific (WHAT the data is).

        If an attribute appears on every entity type, it's provenance by definition.
        No hardcoded list needed — the intersection IS the definition.

        IMPORTANT: Queries TypeDB directly with '$et owns $attr' — does NOT call
        get_entity_fields_from_schema() to avoid infinite recursion.
        """
        if cls._provenance_attrs_cache is not None:
            return cls._provenance_attrs_cache

        driver = typedb_client.driver
        if not driver:
            logger.error("Cannot discover provenance attrs — TypeDB driver not connected")
            cls._provenance_attrs_cache = set()
            return cls._provenance_attrs_cache

        db_name = settings.typedb_database

        # Step 1: Collect all concrete entity types from seed data (READ tx)
        entity_types = set()
        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            # Single-instance entity types (from _exists annotations)
            q1 = """
                match
                    (question: $q) isa question_annotates_attribute,
                        has target_entity_type $et,
                        has target_attribute_name "_exists";
                    $q has question_id $qid;
                select $et;
            """
            for row in tx.query(q1).resolve().as_concept_rows():
                et = row.get("et")
                try:
                    entity_types.add(et.as_attribute().get_value())
                except Exception:
                    try:
                        entity_types.add(et.as_value().get())
                    except Exception:
                        pass

            # Multi-instance entity types (from entity_list questions)
            q2 = """
                match
                    $q isa ontology_question,
                        has answer_type "entity_list",
                        has target_entity_type $et;
                select $et;
            """
            for row in tx.query(q2).resolve().as_concept_rows():
                et = row.get("et")
                try:
                    entity_types.add(et.as_attribute().get_value())
                except Exception:
                    try:
                        entity_types.add(et.as_value().get())
                    except Exception:
                        pass
        finally:
            tx.close()

        entity_types.discard("rp_provision")

        if not entity_types:
            cls._provenance_attrs_cache = set()
            return cls._provenance_attrs_cache

        # Step 2: For each entity type, query owned attrs directly (SCHEMA tx)
        # Does NOT call get_entity_fields_from_schema to avoid recursion
        all_attr_sets = []
        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            for et in entity_types:
                attrs = set()
                try:
                    query = f"match $et label {et}; $et owns $attr; select $attr;"
                    for row in tx.query(query).resolve().as_concept_rows():
                        attrs.add(row.get("attr").as_attribute_type().get_label())
                except Exception:
                    pass

                if not attrs:
                    # Abstract type — try subtypes
                    try:
                        sub_query = f"""
                            match $sub sub {et};
                            not {{ $sub label {et}; }};
                            $sub owns $attr;
                            select $sub, $attr;
                        """
                        sub_attrs: dict = {}
                        for row in tx.query(sub_query).resolve().as_concept_rows():
                            sub_name = row.get("sub").as_entity_type().get_label()
                            attr_name = row.get("attr").as_attribute_type().get_label()
                            sub_attrs.setdefault(sub_name, set()).add(attr_name)
                        for sub_name, sa in sub_attrs.items():
                            all_attr_sets.append(sa)
                    except Exception:
                        pass
                else:
                    all_attr_sets.append(attrs)
        except Exception as e:
            logger.warning(f"Provenance attr discovery failed: {e}")
        finally:
            if tx.is_open():
                tx.close()

        if not all_attr_sets:
            logger.error("Provenance attr discovery returned empty — no entity types found in TypeDB")
            cls._provenance_attrs_cache = set()
            return cls._provenance_attrs_cache

        # Intersection = attributes that appear on every entity type
        provenance = set.intersection(*all_attr_sets)
        provenance = {a for a in provenance if not a.endswith("_id")}

        if not provenance:
            logger.error("Provenance attr intersection is empty — entity types share no common attributes")

        cls._provenance_attrs_cache = provenance
        logger.info(f"Discovered provenance attrs ({len(provenance)}): {sorted(provenance)}")
        return provenance

    @classmethod
    def get_entity_fields_from_schema(cls, entity_type: str, _tx=None) -> Dict[str, Any]:
        """Query TypeDB SCHEMA transaction to discover entity attributes.

        Classification:
        1. @key attributes → skip (system IDs)
        2. Provenance attrs (schema intersection) → skip from field list (appended as standard fields)
        3. Everything else → extractable fields

        For abstract types: introspect subtypes and their additional attributes.
        Returns dict with is_abstract, common_fields, subtypes (if abstract).

        Args:
            _tx: Optional existing SCHEMA transaction to reuse (avoids opening a new one).
        """
        if entity_type in cls._entity_fields_cache:
            return cls._entity_fields_cache[entity_type]

        driver = typedb_client.driver
        if not driver:
            logger.warning("No TypeDB driver for schema introspection")
            return {"is_abstract": False, "fields": [], "subtypes": {}}

        db_name = settings.typedb_database
        own_tx = _tx is None
        tx = _tx if _tx else driver.transaction(db_name, TransactionType.READ)
        try:
            result = cls._introspect_entity_type(tx, entity_type)
            cls._entity_fields_cache[entity_type] = result
            return result
        except Exception as e:
            logger.error(f"Schema introspection failed for {entity_type}: {e}")
            return {"is_abstract": False, "fields": [], "subtypes": {}}
        finally:
            if own_tx and tx.is_open():
                tx.close()

    @classmethod
    def _introspect_entity_type(cls, tx, entity_type: str) -> Dict[str, Any]:
        """Introspect a single entity type in a schema transaction.

        TypeDB 3.x notes:
        - Use `$et label X` or `$et sub X` (not `entity $et type X`)
        - @key cannot be queried in match (QueryingAnnotations not implemented)
        - Key attrs identified by *_id suffix convention
        """
        # Get all owned attributes
        all_attrs = set()
        try:
            all_query = f"""
                match $et label {entity_type}; $et owns $attr_type;
                select $attr_type;
            """
            for row in tx.query(all_query).resolve().as_concept_rows():
                all_attrs.add(row.get("attr_type").as_attribute_type().get_label())
        except Exception:
            pass

        # @key querying not implemented in TypeDB 3.x — identify by *_id convention
        key_attrs = {a for a in all_attrs if a.endswith("_id")}

        # Extractable = all - key (provenance filtering deferred to callers)
        extractable = sorted(all_attrs - key_attrs)

        # Check for subtypes
        subtypes = {}
        try:
            sub_query = f"""
                match $sub sub {entity_type};
                not {{ $sub label {entity_type}; }};
                select $sub;
            """
            sub_rows = list(tx.query(sub_query).resolve().as_concept_rows())
            for row in sub_rows:
                sub_label = row.get("sub").as_entity_type().get_label()
                sub_info = cls._introspect_entity_type(tx, sub_label)
                subtypes[sub_label] = sub_info
        except Exception:
            pass

        is_abstract = len(subtypes) > 0

        if is_abstract:
            # Common fields = fields on the abstract type itself
            return {
                "is_abstract": True,
                "common_fields": extractable,
                "subtypes": subtypes,
            }
        else:
            return {
                "is_abstract": False,
                "fields": extractable,
            }

    @classmethod
    def get_key_attr_for_entity(cls, entity_type: str, _tx=None) -> Optional[str]:
        """Get the @key attribute name for an entity type. Cached.

        TypeDB 3.x doesn't support querying @key annotations in match clauses.
        We query all owned attrs and identify the key by *_id suffix convention.

        Args:
            _tx: Optional existing SCHEMA transaction to reuse.
        """
        if entity_type in cls._key_attr_cache:
            return cls._key_attr_cache[entity_type]

        driver = typedb_client.driver
        if not driver:
            return None

        db_name = settings.typedb_database
        own_tx = _tx is None
        tx = _tx if _tx else driver.transaction(db_name, TransactionType.READ)
        try:
            query = f"""
                match $et label {entity_type}; $et owns $attr;
                select $attr;
            """
            for row in tx.query(query).resolve().as_concept_rows():
                attr_name = row.get("attr").as_attribute_type().get_label()
                if attr_name.endswith("_id"):
                    cls._key_attr_cache[entity_type] = attr_name
                    return attr_name
        except Exception as e:
            logger.error(f"Failed to get @key for {entity_type}: {e}")
        finally:
            if own_tx and tx.is_open():
                tx.close()

        return None

    _attr_value_type_cache: Dict[str, Dict[str, str]] = {}

    @classmethod
    @staticmethod
    def _resolve_attr_value_type(attr_type) -> str:
        """Resolve value type from a TypeDB 3.x _AttributeType using is_* methods.

        TypeDB 3.x removed get_value_type() — use is_boolean(), is_string(), etc.
        """
        # try_get_value_type may work on some driver versions
        try:
            vt = attr_type.try_get_value_type()
            if vt is not None:
                return str(vt).lower()
        except Exception:
            pass

        # TypeDB 3.x is_* type checks
        if attr_type.is_boolean():
            return "boolean"
        if attr_type.is_integer():
            return "long"
        if attr_type.is_double():
            return "double"
        if attr_type.is_string():
            return "string"
        if attr_type.is_datetime():
            return "datetime"
        if attr_type.is_datetime_tz():
            return "datetime"
        if attr_type.is_date():
            return "datetime"
        if attr_type.is_decimal():
            return "double"
        if attr_type.is_duration():
            return "string"
        return "string"  # Safe fallback

    @classmethod
    def _get_basket_subtype_names(cls) -> List[str]:
        """Introspect all concrete basket subtypes from schema.

        SSoT: discovers types by querying which entity types own basket_id,
        then filters to leaf types (no further subtypes). No hardcoded parent names.
        Used to expand {basket_subtypes} template variable in extraction prompts.
        """
        cache_key = "__basket_subtypes"
        if cache_key in cls._entity_fields_cache:
            return cls._entity_fields_cache[cache_key]

        driver = typedb_client.driver
        if not driver:
            return []

        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # All types that own basket_id (includes abstract parents + all subtypes)
            all_types = set()
            for row in tx.query(
                "match $bt owns basket_id; select $bt;"
            ).resolve().as_concept_rows():
                all_types.add(row.get("bt").as_entity_type().get_label())

            # Filter to concrete (leaf) types: those with no further subtypes
            concrete = set()
            for t in all_types:
                sub_rows = list(tx.query(f"""
                    match $sub sub {t}; not {{ $sub label {t}; }}; select $sub;
                """).resolve().as_concept_rows())
                if not sub_rows:
                    concrete.add(t)

            result = sorted(concrete)
            cls._entity_fields_cache[cache_key] = result
            logger.info(f"Introspected {len(result)} concrete basket subtypes from schema")
            return result
        except Exception as e:
            logger.warning(f"Failed to introspect basket subtypes: {e}")
            return []
        finally:
            tx.close()

    @classmethod
    def _load_cross_covenant_mappings(cls) -> Dict[str, str]:
        """Load basket_type_name -> provision_type_name from TypeDB seed data.

        SSoT: the mapping lives in cross_covenant_mapping entities in TypeDB,
        not in a Python dict. Adding a new cross-covenant basket = one seed insert,
        zero Python changes.
        """
        if cls._cross_covenant_cache is not None:
            return cls._cross_covenant_cache

        driver = typedb_client.driver
        if not driver:
            return {}

        result = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = '''match
                $m isa cross_covenant_mapping,
                    has basket_type_name $bt,
                    has provision_type_name $pt;
                select $bt, $pt;'''
            for row in tx.query(query).resolve().as_concept_rows():
                bt = row.get("bt").as_attribute().get_value()
                pt = row.get("pt").as_attribute().get_value()
                result[bt] = pt
        except Exception as e:
            logger.warning(f"Failed to load cross-covenant mappings: {e}")
        finally:
            tx.close()

        cls._cross_covenant_cache = result
        logger.info(f"Loaded {len(result)} cross-covenant basket mappings from TypeDB")
        return result

    @classmethod
    def _load_capacity_classifications(cls) -> Dict[str, str]:
        """Load basket_type_name -> capacity_category from TypeDB seed data.

        SSoT: classification lives in basket_capacity_class entities.
        Adding a new basket type = one seed insert, zero code changes.
        """
        if cls._capacity_class_cache is not None:
            return cls._capacity_class_cache

        driver = typedb_client.driver
        if not driver:
            return {}

        result = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            for row in tx.query('''match
                $m isa basket_capacity_class,
                    has basket_type_name $bt,
                    has capacity_category $cc;
                select $bt, $cc;''').resolve().as_concept_rows():
                bt = row.get("bt").as_attribute().get_value()
                cc = row.get("cc").as_attribute().get_value()
                result[bt] = cc
        except Exception as e:
            logger.warning(f"Failed to load capacity classifications: {e}")
        finally:
            tx.close()

        cls._capacity_class_cache = result
        return result

    @classmethod
    def _get_relation_attr_types(cls, relation_type: str) -> Dict[str, str]:
        """Get {attr_name: value_type} for all attributes owned by a relation type.

        SSoT: mirrors get_attr_value_types() but for relations instead of entities.
        Used by _build_reallocation_edge_query to avoid hardcoded field maps.
        """
        cache_key = f"__rel_attrs_{relation_type}"
        if cache_key in cls._entity_fields_cache:
            return cls._entity_fields_cache[cache_key]

        driver = typedb_client.driver
        if not driver:
            return {}

        result = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f"match $rt label {relation_type}; $rt owns $attr; select $attr;"
            for row in tx.query(query).resolve().as_concept_rows():
                attr_type = row.get("attr").as_attribute_type()
                attr_name = attr_type.get_label()
                vt = cls._resolve_attr_value_type(attr_type)
                result[attr_name] = vt
        except Exception as e:
            logger.warning(f"Failed to introspect relation attrs for {relation_type}: {e}")
        finally:
            tx.close()

        cls._entity_fields_cache[cache_key] = result
        logger.info(f"Introspected {len(result)} attrs for relation {relation_type}")
        return result

    def get_attr_value_types(cls, entity_type: str) -> Dict[str, str]:
        """Get {attr_name: value_type} for all attributes owned by an entity type. Cached.

        Returns value types like 'string', 'boolean', 'long', 'double', 'datetime'.
        Used to coerce Claude's output to match schema expectations.
        """
        if entity_type in cls._attr_value_type_cache:
            return cls._attr_value_type_cache[entity_type]

        driver = typedb_client.driver
        if not driver:
            return {}

        db_name = settings.typedb_database
        result = {}
        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            query = f"""
                match $et label {entity_type}; $et owns $attr;
                select $attr;
            """
            for row in tx.query(query).resolve().as_concept_rows():
                attr_type = row.get("attr").as_attribute_type()
                attr_name = attr_type.get_label()
                vt = cls._resolve_attr_value_type(attr_type)
                result[attr_name] = vt
        except Exception as e:
            logger.error(f"Failed to get attr value types for {entity_type}: {e}")
        finally:
            if tx.is_open():
                tx.close()

        if result:
            logger.info(f"get_attr_value_types({entity_type}): {len(result)} attrs")
        else:
            logger.error(f"get_attr_value_types({entity_type}): returned EMPTY")

        cls._attr_value_type_cache[entity_type] = result
        return result

    @staticmethod
    def _get_attr(row, key: str, default=None):
        """Safely get attribute value from TypeDB row."""
        try:
            concept = row.get(key)
            if concept is None:
                return default
            return concept.as_attribute().get_value()
        except Exception:
            return default

    @classmethod
    def _prompt_header(cls) -> str:
        """Common response format header shared by entity_list and scalar prompts."""
        return """You are extracting Restricted Payment covenant data from a credit agreement.

Return a single JSON object with one key: `"answers"` — an array of answer objects.

## RESPONSE FORMAT

```json
{
  "answers": [
    {
      "question_id": "rp_a1",
      "value": true,
      "answer_type": "boolean",
      "source_text": "exact verbatim quote from document (max 500 chars)",
      "source_page": 145,
      "section_reference": "6.06(p)"
    },
    {
      "question_id": "rp_el_sweep_tiers",
      "value": [
        {"leverage_threshold": 5.75, "sweep_percentage": 0.5, "is_highest_tier": true,
         "section_reference": "2.10(f)", "source_page": 80}
      ],
      "answer_type": "entity_list"
    }
  ]
}
```

## ANSWER TYPE RULES

- **boolean**: value is true/false (not "yes"/"no")
- **number**: raw numbers (130000000 not "130M", 0.5 not "50%")
- **string**: text string
- **multiselect**: array of concept IDs from the listed options
- **entity_list**: array of objects, each with fields listed per question below

### General rules:
- source_text MUST be exact verbatim quote, not a paraphrase or "See Section X"
- section_reference MUST be the specific agreement provision reference where this appears — including the paragraph/subsection letter or number (e.g., "6.06(p)", "6.09(a)(I)", "Definition of Cumulative Amount, clause (h)", "Clause 22.3(a)"). Be as specific as possible — "6.06(p)" not just "6.06".
- For entity_list answers: section_reference, source_page, and source_text are REQUIRED on EACH entity object. section_reference must be a specific clause (e.g., "6.06(p)", "Definition of Cumulative Amount, clause (h)"), not a generic section. source_page must be the integer page number from the [PAGE X] markers in the document text.
- For percentages use decimals (50% = 0.5, 140% = 1.4)
- For dollar amounts use raw numbers (130000000 not "130M")
- DEFINITIONS: Cross-references ARE definitions. When asked if defined, answer true for both inline and cross-reference.

"""

    @classmethod
    def _prompt_footer(cls, document_text: str) -> str:
        """Common document text + response footer."""
        return f"""
## DOCUMENT TEXT

{document_text}

## RESPONSE

Return ONLY the JSON object with {{"answers": [...]}}. No markdown, no explanation."""

    @classmethod
    def build_entity_list_prompt(
        cls,
        entity_list_questions: List[Dict],
        document_text: str,
    ) -> str:
        """Build prompt for entity_list extraction only.

        Sends only entity extraction questions (sweep_tiers, de_minimis, etc.)
        to get full coverage of structured entities in a dedicated call.
        """
        prompt = cls._prompt_header()

        prompt += "You MUST return an answer for ALL entity_list questions below. "
        prompt += "If no entities of that type exist, return the question with value: []\n\n"

        prompt += "## ENTITY EXTRACTION\n\n"
        prompt += "For each entity_list question below, return an array of entity objects.\n"
        prompt += "Each entity object should include the listed fields plus provenance (section_reference, source_page, source_text).\n\n"

        for q in sorted(entity_list_questions, key=lambda x: x.get("display_order", 0)):
            prompt += cls._format_entity_list_question(q)

        prompt += cls._prompt_footer(document_text)
        return prompt

    @classmethod
    def build_scalar_prompt(
        cls,
        questions_by_cat: Dict[str, List],
        document_text: str,
    ) -> str:
        """Build prompt for scalar/multiselect questions only.

        Sends a batch of categorized questions. Used in batched extraction
        to stay within output token limits.
        """
        prompt = cls._prompt_header()

        prompt += "You MUST answer ALL questions listed below. "
        prompt += 'For questions where the answer cannot be found in the document, respond with value: null.\n\n'

        prompt += "## QUESTIONS\n\n"

        for cat_id in sorted(questions_by_cat.keys()):
            cat_questions = questions_by_cat[cat_id]
            if not cat_questions:
                continue

            cat_name = cat_questions[0].get("category_name", cat_id)
            prompt += f"### Category {cat_id}: {cat_name} ({len(cat_questions)} questions)\n\n"

            for q in sorted(cat_questions, key=lambda x: x.get("display_order", 0)):
                answer_type = q.get("answer_type", "string")
                qid = q["question_id"]
                text = q.get("question_text", "")
                prompt += f"- **{qid}**: \"{text}\" ({answer_type})\n"

                hint = q.get("extraction_prompt")
                if hint:
                    prompt += f"  Hint: {hint}\n"

                if answer_type == "multiselect" and q.get("concept_options"):
                    opts = ", ".join(
                        f"{opt['id']} ({opt['name']})"
                        for opt in q["concept_options"]
                    )
                    prompt += f"  Valid options: [{opts}]\n"

            prompt += "\n"

        prompt += cls._prompt_footer(document_text)
        return prompt

    @classmethod
    def _format_entity_list_question(cls, q: Dict) -> str:
        """Format a single entity_list question with schema-introspected fields."""
        qid = q["question_id"]
        text = q.get("question_text", "")
        entity_type = q.get("target_entity_type", "")
        hint = q.get("extraction_prompt", "")

        section = f"### {qid}: {text}\n"
        section += f"- answer_type: entity_list\n"
        section += f"- entity_type: {entity_type}\n"

        if hint:
            # Expand template variables (SSoT: lists from schema, not hardcoded)
            if "{basket_subtypes}" in hint:
                basket_types = cls._get_basket_subtype_names()
                hint = hint.replace("{basket_subtypes}", ", ".join(basket_types))
            section += f"- instructions: {hint}\n"

        # Introspect schema for fields
        schema_info = cls.get_entity_fields_from_schema(entity_type)

        # Fields set by SSoT seed data, not by extraction
        ssot_only_fields = {"capacity_category"}

        if schema_info.get("is_abstract"):
            section += f"- **This is an abstract type with subtypes.** Include a `\"type\"` field to specify the subtype.\n"
            common = [f for f in schema_info.get("common_fields", []) if f not in ssot_only_fields]
            if common:
                section += f"- Common fields: {', '.join(common)}\n"
            for sub_name, sub_info in schema_info.get("subtypes", {}).items():
                sub_fields = sub_info.get("fields", [])
                if sub_fields:
                    section += f"  - **{sub_name}**: {', '.join(sub_fields)}\n"
        else:
            fields = [f for f in schema_info.get("fields", []) if f not in ssot_only_fields]
            if fields:
                section += f"- Fields: {', '.join(fields)}\n"

        section += f"- **REQUIRED provenance on EVERY entity**: section_reference (e.g., '6.06(p)'), source_page (integer page number), source_text (verbatim quote, max 500 chars)\n"
        section += "\n"
        return section

    @classmethod
    def parse_extraction_response(cls, response_text: str) -> ExtractionResponse:
        """
        Parse Claude's JSON response into ExtractionResponse.

        Returns:
            ExtractionResponse with typed Answer objects
        """
        # Extract JSON from response (handle markdown code blocks)
        json_text = response_text.strip()

        if "```json" in response_text:
            json_text = response_text.split("```json")[1].split("```")[0]
        elif "```" in response_text:
            parts = response_text.split("```")
            if len(parts) >= 2:
                json_text = parts[1]
                if json_text.startswith("JSON") or json_text.startswith("json"):
                    json_text = json_text[4:]

        json_text = json_text.strip()

        # Find JSON object boundaries
        start_idx = json_text.find("{")
        end_idx = json_text.rfind("}") + 1

        if start_idx == -1 or end_idx == 0:
            logger.error(f"No JSON object found in response: {response_text[:500]}")
            raise ValueError("No JSON object found in Claude response")

        json_text = json_text[start_idx:end_idx]

        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Response text: {json_text[:500]}")
            raise ValueError(f"Failed to parse Claude response as JSON: {e}")

        # Build answers list
        answers = []
        raw_answers = data.get("answers", [])
        counts = {"boolean": 0, "number": 0, "string": 0, "multiselect": 0, "entity_list": 0}

        for raw in raw_answers:
            try:
                answer = Answer(
                    question_id=raw.get("question_id", ""),
                    value=raw.get("value"),
                    answer_type=raw.get("answer_type", "string"),
                    source_text=raw.get("source_text") or "",
                    source_page=raw.get("source_page"),
                    section_reference=raw.get("section_reference"),
                    reasoning=raw.get("reasoning"),
                )
                answers.append(answer)
                at = answer.answer_type
                if at in counts:
                    counts[at] += 1
            except Exception as e:
                logger.warning(f"Skipping malformed answer: {e}")

        logger.info(
            f"Parsed extraction response: {len(answers)} answers "
            f"(bool={counts['boolean']}, num={counts['number']}, str={counts['string']}, "
            f"multi={counts['multiselect']}, entity_list={counts['entity_list']})"
        )
        return ExtractionResponse(answers=answers)

    # ═══════════════════════════════════════════════════════════════════════════
    # ROUTING TABLE LOADERS — cached TypeDB lookups for entity storage
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def _load_question_to_entity_map(cls) -> Dict[str, tuple]:
        """Load question_id → (entity_type, attr_name) from TypeDB.

        Reverse of graph_reader._get_annotation_map().
        Source: question_annotates_attribute relations (Phase 2b/2c).
        """
        if cls._q_to_entity_cache is not None:
            return cls._q_to_entity_cache

        driver = typedb_client.driver
        if not driver:
            return {}

        query = """
            match
                (question: $q) isa question_annotates_attribute,
                    has target_entity_type $et,
                    has target_attribute_name $an;
                $q has question_id $qid;
            select $qid, $et, $an;
        """
        result = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            for row in tx.query(query).resolve().as_concept_rows():
                qid = cls._get_attr(row, "qid")
                et = cls._get_attr(row, "et")
                an = cls._get_attr(row, "an")
                if qid and et and an:
                    result[qid] = (et, an)
        finally:
            tx.close()

        cls._q_to_entity_cache = result
        logger.info(f"Loaded {len(result)} question->entity annotations")
        return result

    @classmethod
    def _load_concept_routing_map(cls) -> Dict[str, List]:
        """Load concept_id → [(entity_type, attribute_name), ...] from TypeDB.

        One-to-many: a concept can set multiple booleans (e.g. bt_at_both → 2 attrs).
        Source: concept instances with target_entity_type + target_entity_attribute (Phase 2a).
        """
        if cls._concept_routing_cache is not None:
            return cls._concept_routing_cache

        driver = typedb_client.driver
        if not driver:
            return {}

        query = """
            match
                $c isa concept,
                    has concept_id $cid,
                    has target_entity_type $et,
                    has target_entity_attribute $ea;
            select $cid, $et, $ea;
        """
        result: Dict[str, List] = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            for row in tx.query(query).resolve().as_concept_rows():
                cid = cls._get_attr(row, "cid")
                et = cls._get_attr(row, "et")
                ea = cls._get_attr(row, "ea")
                if cid and et and ea:
                    result.setdefault(cid, []).append((et, ea))
        finally:
            tx.close()

        cls._concept_routing_cache = result
        total = sum(len(v) for v in result.values())
        logger.info(f"Loaded {total} concept->entity boolean mappings ({len(result)} concepts)")
        return result

    @classmethod
    def _load_entity_list_types(cls, _tx=None) -> set:
        """Load entity types created by entity_list questions. Skip these for _exists creation.

        Expands abstract types to include subtypes (e.g. builder_basket_source → ecf_source, etc.).

        Args:
            _tx: Optional existing SCHEMA transaction to reuse for subtype expansion.
        """
        if cls._entity_list_types_cache is not None:
            return cls._entity_list_types_cache

        driver = typedb_client.driver
        if not driver:
            return set()

        query = """
            match
                $q isa ontology_question,
                    has answer_type "entity_list",
                    has target_entity_type $et;
            select $et;
        """
        types = set()
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            for row in tx.query(query).resolve().as_concept_rows():
                et = cls._get_attr(row, "et")
                if et:
                    types.add(et)
        finally:
            tx.close()

        # Expand with subtypes for abstract entity types
        expanded = set(types)
        for et in types:
            schema_info = cls.get_entity_fields_from_schema(et, _tx=_tx)
            if schema_info.get("is_abstract"):
                for sub in schema_info.get("subtypes", {}).keys():
                    expanded.add(sub)

        cls._entity_list_types_cache = expanded
        logger.info(f"Entity list types ({len(expanded)}): {sorted(expanded)}")
        return expanded

    # Provision types to discover entity relations for
    _PROVISION_TYPES = ("rp_provision", "mfn_provision", "di_provision")

    @classmethod
    def _load_entity_relation_map(cls, _tx=None) -> Dict[str, tuple]:
        """Discover entity→provision relation from TypeDB schema introspection. Cached.

        For each single-instance entity type (from _exists annotations, minus entity_list types),
        queries the SCHEMA to find which relation links it to a provision type (rp or mfn),
        including inherited plays declarations.

        Returns: {entity_type: (relation_type, provision_role, entity_role)}

        Args:
            _tx: Optional existing SCHEMA transaction to reuse.
        """
        if cls._entity_relation_cache is not None:
            return cls._entity_relation_cache

        driver = typedb_client.driver
        if not driver:
            return {}

        q_to_entity = cls._load_question_to_entity_map()
        entity_list_types = cls._load_entity_list_types(_tx=_tx)

        # Collect entity types from _exists annotations, minus entity_list types and provision types
        provision_labels = set(cls._PROVISION_TYPES)
        target_types = set()
        for qid, (et, attr) in q_to_entity.items():
            if attr == "_exists" and et not in entity_list_types and et not in provision_labels:
                target_types.add(et)

        result = {}
        own_tx = _tx is None
        tx = _tx if _tx else driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            for et in sorted(target_types):
                found = False
                for prov_type in cls._PROVISION_TYPES:
                    query = f"""
                        match
                            $et1 label {et}; $et1 plays $role1;
                            $et2 label {prov_type}; $et2 plays $role2;
                            relation $rt; $rt relates $role1; $rt relates $role2;
                        select $rt, $role1, $role2;
                    """
                    try:
                        rows = list(tx.query(query).resolve().as_concept_rows())
                        if rows:
                            row = rows[0]
                            rt = row.get("rt").as_relation_type().get_label()
                            role1_raw = row.get("role1").get_label()
                            role2_raw = row.get("role2").get_label()
                            role1 = role1_raw.split(":")[-1] if ":" in role1_raw else role1_raw
                            role2 = role2_raw.split(":")[-1] if ":" in role2_raw else role2_raw
                            result[et] = (rt, role2, role1)
                            logger.debug(f"Schema: {et} -> {rt} ({role2}, {role1}) via {prov_type}")
                            found = True
                            break
                    except Exception as e:
                        logger.warning(f"Schema introspection failed for {et} via {prov_type}: {e}")
                if not found:
                    logger.warning(f"No provision relation found for {et}")
        finally:
            if own_tx and tx.is_open():
                tx.close()

        cls._entity_relation_cache = result
        logger.info(f"Loaded entity->relation map for {len(result)} types: {sorted(result.keys())}")
        return result

    @classmethod
    def _load_storage_value_types(cls) -> Dict[str, str]:
        """Load question_id → storage_value_type mapping from TypeDB. Cached."""
        if cls._storage_value_type_cache is not None:
            return cls._storage_value_type_cache

        driver = typedb_client.driver
        if not driver:
            return {}

        result = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = '''match
                $q isa ontology_question, has question_id $qid, has storage_value_type $svt;
                select $qid, $svt;'''
            for row in tx.query(query).resolve().as_concept_rows():
                qid = row.get("qid").as_attribute().get_value()
                svt = row.get("svt").as_attribute().get_value()
                result[qid] = svt
        except Exception as e:
            logger.warning(f"Could not load storage_value_types: {e}")
        finally:
            if tx.is_open():
                tx.close()

        cls._storage_value_type_cache = result
        logger.info(f"Loaded storage_value_type for {len(result)} questions")
        return result

    def _get_storage_value_type(self, question_id: str) -> Optional[str]:
        """Get the storage_value_type for a question. Returns None if not seeded."""
        svt_map = self._load_storage_value_types()
        return svt_map.get(question_id)

    # ═══════════════════════════════════════════════════════════════════════════
    # SINGLE-INSTANCE ENTITY CREATION + ATTRIBUTE POPULATION
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_section_ref(source_text: str) -> Optional[str]:
        """Extract section reference from source text if not explicitly provided."""
        if not source_text:
            return None
        match = re.search(r'(Section \d+\.\d+(?:\([a-z]\))?(?:\([a-z]+\))?)', source_text)
        if match:
            return match.group(1)
        match = re.search(r'(Definition of [A-Z][^,;.]+)', source_text)
        if match:
            return match.group(1).strip()
        return None

    @staticmethod
    def _provision_type_from_id(provision_id: str) -> str:
        """Determine provision type from provision_id suffix convention."""
        suffix = provision_id.rsplit("_", 1)[-1]
        suffix_map = {"mfn": "mfn_provision", "di": "di_provision"}
        return suffix_map.get(suffix, "rp_provision")

    def _create_single_instance_entity(self, provision_id: str, entity_type: str,
                                        source_text: str = None, source_page: int = None,
                                        section_reference: str = None):
        """Create a single-instance entity and link to provision via schema-introspected relation.

        Called when an _exists answer is True (e.g., rp_k1=true → create jcrew_blocker).
        Propagates provenance attributes (source_text, source_page, section_reference)
        from the _exists answer onto the entity.
        """
        entity_relation_map = self._load_entity_relation_map()
        relation_info = entity_relation_map.get(entity_type)
        if not relation_info:
            logger.warning(f"No relation mapping for single-instance entity: {entity_type}")
            return

        relation_type, prov_role, entity_role = relation_info
        key_attr = self.get_key_attr_for_entity(entity_type)
        if not key_attr:
            logger.warning(f"No @key attribute found for {entity_type}")
            return

        entity_id = f"{provision_id}_{entity_type}"
        prov_type = self._provision_type_from_id(provision_id)

        # Build provenance attributes (only for attrs the entity type actually owns)
        attr_types = self.get_attr_value_types(entity_type)
        prov_attrs = []
        if source_text and "source_text" in attr_types:
            prov_attrs.append(f'has source_text "{self._escape(source_text[:2000])}"')
        if source_page is not None and "source_page" in attr_types:
            prov_attrs.append(f'has source_page {source_page}')
        if section_reference and "section_reference" in attr_types:
            prov_attrs.append(f'has section_reference "{self._escape(section_reference)}"')

        prov_str = ""
        if prov_attrs:
            prov_str = ",\n                    ".join([""] + prov_attrs)  # leading comma

        query = f'''
            match
                $prov isa {prov_type}, has provision_id "{provision_id}";
            insert
                $entity isa {entity_type},
                    has {key_attr} "{entity_id}"{prov_str};
                ({prov_role}: $prov, {entity_role}: $entity) isa {relation_type};
        '''
        try:
            self._execute_query(query)
            logger.info(f"Created {entity_type}: {entity_id} (provenance: {len(prov_attrs)} attrs)")

            # Set capacity_category from SSoT classification
            cap_cat = self._load_capacity_classifications().get(entity_type)
            if cap_cat:
                try:
                    self._execute_query(f'''
                        match $b isa {entity_type}, has {key_attr} "{entity_id}";
                            not {{ $b has capacity_category $existing; }};
                        insert $b has capacity_category "{cap_cat}";
                    ''')
                except Exception:
                    pass  # Already set or type doesn't support it
        except Exception as e:
            logger.warning(f"Failed to create {entity_type}: {e}")

    def _set_entity_attribute(self, provision_id: str, entity_type: str, attr_name: str, value):
        """Set an attribute on an existing single-instance entity.

        For single-instance entities: entity_id is {provision_id}_{entity_type}.
        Silently no-ops if entity doesn't exist (match returns nothing).
        """
        if value is None:
            return

        key_attr = self.get_key_attr_for_entity(entity_type)
        if not key_attr:
            return

        # Provision subtypes use provision_id directly as their key;
        # child entities use {provision_id}_{entity_type} convention.
        if key_attr == "provision_id":
            entity_id = provision_id
        else:
            entity_id = f"{provision_id}_{entity_type}"

        # Use shared schema-based coercion (SSoT)
        attr_types = self.get_attr_value_types(entity_type)
        tql_value = self._format_tql_value(value, attr_types.get(attr_name))
        if tql_value is None:
            logger.warning(f"Cannot coerce {value!r} for {entity_type}.{attr_name}")
            return

        query = f'''
            match
                $entity isa {entity_type}, has {key_attr} "{entity_id}";
            insert
                $entity has {attr_name} {tql_value};
        '''
        try:
            self._execute_query(query)
        except Exception as e:
            logger.debug(f"Could not set {entity_type}.{attr_name}: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # UNIFIED STORAGE — store_extraction()
    # ═══════════════════════════════════════════════════════════════════════════

    # ───────────────────────────────────────────────────────────────────────
    # RELATION CONFIG — schema introspection (replaces hardcoded dict)
    # ───────────────────────────────────────────────────────────────────────

    # Infrastructure relations to exclude from Tier 3 discovery
    _INFRA_RELATIONS = frozenset({
        "provision_has_answer", "concept_applicability", "deal_has_provision",
        "provision_cross_reference", "has_provenance", "answer_has_qualification",
        "answer_has_citation", "category_has_question", "question_annotates_attribute",
        "deal_has_document", "question_maps_concept",
    })

    @classmethod
    def _build_relation_config(cls) -> Dict[str, Dict]:
        """Introspect TypeDB schema to build relation storage config.

        Discovers relations across 3 tiers:
          Tier 1: provision_has_extracted_entity subs (provision → entity)
          Tier 2: entity_has_child subs (entity → child, via provision)
          Tier 3: other relations where a provision type plays a role

        Returns: {relation_label: {parent_match_template, roles, parent_var}}
        """
        driver = typedb_client.driver
        if not driver:
            raise RuntimeError("Cannot build relation config — TypeDB not connected")

        config: Dict[str, Dict] = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            # ── Tier 1: provision_has_extracted_entity subs ─────────────
            tier1_query = """
                match
                    relation $rel;
                    $rel sub provision_has_extracted_entity;
                    $rel relates $role;
                    not { $rel label provision_has_extracted_entity; };
                select $rel, $role;
            """
            tier1_rows = list(tx.query(tier1_query).resolve().as_concept_rows())

            rel_roles: Dict[str, list] = {}
            for row in tier1_rows:
                rl = row.get("rel").as_relation_type().get_label()
                ro = row.get("role").get_label()
                ro = ro.split(":")[-1] if ":" in ro else ro
                rel_roles.setdefault(rl, []).append(ro)

            for rl, roles in rel_roles.items():
                entity_roles = [r for r in roles if r not in ("provision", "extracted")]
                if len(entity_roles) != 1:
                    logger.warning(f"Tier 1 skip {rl}: unexpected roles {roles}")
                    continue
                config[rl] = {
                    "parent_match_template": '$prov isa {prov_type}, has provision_id "{provision_id}";',
                    "roles": ("provision", entity_roles[0]),
                    "parent_var": "$prov",
                }

            logger.info(f"Tier 1 (provision_has_extracted_entity subs): {len(config)} relations")

            # ── Tier 2: entity_has_child subs ──────────────────────────
            tier2_query = """
                match
                    relation $rel;
                    $rel sub entity_has_child;
                    $rel relates $role;
                    not { $rel label entity_has_child; };
                select $rel, $role;
            """
            tier2_rows = list(tx.query(tier2_query).resolve().as_concept_rows())

            child_rel_roles: Dict[str, list] = {}
            for row in tier2_rows:
                rl = row.get("rel").as_relation_type().get_label()
                ro = row.get("role").get_label()
                ro = ro.split(":")[-1] if ":" in ro else ro
                child_rel_roles.setdefault(rl, []).append(ro)

            tier2_count = 0
            for rl, roles in child_rel_roles.items():
                concrete_roles = [r for r in roles if r not in ("parent", "child")]
                if len(concrete_roles) != 2:
                    logger.warning(f"Tier 2 skip {rl}: expected 2 concrete roles, got {concrete_roles}")
                    continue

                # Find which Tier 1 relation has a matching entity role for one of these
                parent_role = None
                child_role = None
                parent_provision_rel = None
                for cr in concrete_roles:
                    for cfg_rl, cfg in config.items():
                        if cfg["roles"][1] == cr:
                            parent_role = cr
                            parent_provision_rel = cfg_rl
                            break
                    if parent_role:
                        break

                if not parent_role or not parent_provision_rel:
                    logger.warning(f"Tier 2 skip {rl}: no Tier 1 parent found for roles {concrete_roles}")
                    continue

                child_role = [r for r in concrete_roles if r != parent_role][0]
                prov_cfg = config[parent_provision_rel]
                prov_entity_role = prov_cfg["roles"][1]

                config[rl] = {
                    "parent_match_template": (
                        '$prov isa {prov_type}, has provision_id "{provision_id}";'
                        f'\n                ({prov_cfg["roles"][0]}: $prov, {prov_entity_role}: $parent) isa {parent_provision_rel};'
                    ),
                    "roles": (parent_role, child_role),
                    "parent_var": "$parent",
                }
                tier2_count += 1

            logger.info(f"Tier 2 (entity_has_child subs): {tier2_count} relations")

            # ── Tier 3: other relations where provision plays a role ───
            # Catches has_amendment_threshold where provision plays entity_with_threshold
            tier3_count = 0
            for prov_type in ("rp_provision", "mfn_provision"):
                # Query which roles this provision type plays
                tier3_query = f"""
                    match
                        entity $pt; $pt label {prov_type}; $pt plays $role;
                        relation $rel; $rel relates $role;
                    select $rel, $role;
                """
                try:
                    tier3_rows = list(tx.query(tier3_query).resolve().as_concept_rows())
                except Exception as e:
                    logger.warning(f"Tier 3 query failed for {prov_type}: {e}")
                    continue

                # Group: relation → [roles played by provision]
                prov_plays: Dict[str, list] = {}
                for row in tier3_rows:
                    rl = row.get("rel").as_relation_type().get_label()
                    ro = row.get("role").get_label()
                    ro = ro.split(":")[-1] if ":" in ro else ro
                    prov_plays.setdefault(rl, []).append(ro)

                for rl, prov_roles in prov_plays.items():
                    if rl in config or rl in cls._INFRA_RELATIONS:
                        continue

                    # Get ALL roles for this relation
                    all_roles_query = f"""
                        match
                            relation $rel; $rel label {rl}; $rel relates $role;
                        select $role;
                    """
                    try:
                        all_role_rows = list(tx.query(all_roles_query).resolve().as_concept_rows())
                        all_roles = []
                        for arr in all_role_rows:
                            rn = arr.get("role").get_label()
                            rn = rn.split(":")[-1] if ":" in rn else rn
                            all_roles.append(rn)
                    except Exception:
                        continue

                    # The provision role is what prov_type plays; entity role is the other
                    prov_role = prov_roles[0]
                    other_roles = [r for r in all_roles if r != prov_role and r not in ("extracted", "parent", "child")]
                    if len(other_roles) != 1:
                        continue

                    config[rl] = {
                        "parent_match_template": '$prov isa {prov_type}, has provision_id "{provision_id}";',
                        "roles": (prov_role, other_roles[0]),
                        "parent_var": "$prov",
                    }
                    tier3_count += 1

            logger.info(f"Tier 3 (provision plays non-standard role): {tier3_count} relations")
            logger.info(
                f"Built relation config from schema: {len(config)} total — "
                f"{sorted(config.keys())}"
            )

        except Exception as e:
            logger.error(f"_build_relation_config failed: {e}")
            raise
        finally:
            if tx.is_open():
                tx.close()

        return config

    @classmethod
    def _get_relation_config(cls, target_relation_type: str, provision_id: str) -> Optional[Dict]:
        """Get storage config for a relation type via schema introspection.

        Returns dict with keys: parent_match (formatted), roles, parent_var.
        Replaces the hardcoded _RELATION_CONFIG dict.
        """
        if cls._relation_config_cache is None:
            cls._relation_config_cache = cls._build_relation_config()

        template = cls._relation_config_cache.get(target_relation_type)
        if not template:
            logger.error(f"No relation config found for {target_relation_type}")
            return None

        # Derive provision type from provision_id suffix
        suffix = provision_id.rsplit("_", 1)[-1]  # "rp" or "mfn"
        prov_type = f"{suffix}_provision"

        return {
            "parent_match": template["parent_match_template"].format(
                prov_type=prov_type, provision_id=provision_id
            ),
            "roles": template["roles"],
            "parent_var": template.get("parent_var", "$prov"),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # REALLOCATION GRAPH EDGE WIRING
    # ═══════════════════════════════════════════════════════════════════════════

    def wire_reallocation_edges(self, deal_id: str, provision_id: str,
                                raw_reallocation_items: List[Dict]):
        """Create basket_reallocates_to relation instances from extracted reallocation data.

        Called AFTER _store_entity_list() so all basket entities exist.
        Reads source_basket_type / target_basket_type from the RAW answer JSON
        (these fields are NOT schema attributes on basket_reallocation — they are
        dropped by _store_single_entity(). We read them from the original answer.value.)

        Batches all inserts in a single WRITE transaction to avoid gRPC congestion.
        """
        if not raw_reallocation_items:
            return

        cross_covenant = self._load_cross_covenant_mappings()

        # Phase A: Ensure cross-covenant provisions and baskets exist
        # (must happen before the edge inserts that reference them)
        cross_baskets_needed = set()
        for item in raw_reallocation_items:
            if not isinstance(item, dict):
                continue
            for field in ("source_basket_type", "target_basket_type"):
                bt = item.get(field)
                if bt and bt in cross_covenant:
                    cross_baskets_needed.add(bt)

        for basket_type in cross_baskets_needed:
            prov_type = cross_covenant[basket_type]
            cross_prov_id = f"{deal_id}_{prov_type.replace('_provision', '')}"
            basket_id = f"{cross_prov_id}_{basket_type}"
            self._ensure_cross_provision(deal_id, cross_prov_id, prov_type)
            # Find the first item that references this basket type for dollar amount
            ref_item = next(
                (i for i in raw_reallocation_items
                 if isinstance(i, dict)
                 and (i.get("source_basket_type") == basket_type
                      or i.get("target_basket_type") == basket_type)),
                {}
            )
            self._ensure_cross_basket(cross_prov_id, prov_type, basket_type, basket_id, ref_item)

        # Phase B: Resolve all basket IDs and batch edge inserts
        edge_queries = []
        for item in raw_reallocation_items:
            if not isinstance(item, dict):
                continue

            source_type = item.get("source_basket_type")
            target_type = item.get("target_basket_type")

            if not source_type or not target_type:
                logger.warning(f"Reallocation missing source/target type: "
                             f"{item.get('reallocation_source', '?')}")
                continue

            source_id = self._resolve_basket_id(deal_id, provision_id, source_type, cross_covenant)
            target_id = self._resolve_basket_id(deal_id, provision_id, target_type, cross_covenant)

            if not source_id or not target_id:
                logger.warning(f"Could not resolve basket IDs: "
                             f"{source_type}({source_id}) -> {target_type}({target_id})")
                continue

            query = self._build_reallocation_edge_query(
                source_type, source_id, target_type, target_id, item
            )
            if query:
                edge_queries.append(query)

        # Execute all edge inserts in one WRITE transaction
        if edge_queries:
            tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.WRITE)
            try:
                for q in edge_queries:
                    tx.query(q).resolve()
                tx.commit()
                logger.info(f"Committed {len(edge_queries)} reallocation edges for {provision_id}")
            except Exception as e:
                logger.error(f"Failed to commit reallocation edges: {e}")
                tx.close()
                raise

    def _resolve_basket_id(self, deal_id: str, provision_id: str,
                           basket_type: str, cross_covenant: Dict[str, str]) -> Optional[str]:
        """Resolve a basket type name to its actual @key ID in TypeDB.

        For cross-covenant baskets: derive from known ID convention.
        For RP/RDP baskets: query TypeDB using polymorphic provision_has_extracted_entity.

        Cannot assume key convention for RP/RDP baskets because entity_list baskets
        have non-deterministic index suffixes (e.g., general_rdp_basket_4).

        SSoT: uses provision_has_extracted_entity (abstract parent of all entity-bearing
        relations) instead of hardcoded relation type names.
        """
        if basket_type in cross_covenant:
            prov_type = cross_covenant[basket_type]
            cross_prov_id = f"{deal_id}_{prov_type.replace('_provision', '')}"
            return f"{cross_prov_id}_{basket_type}"

        # Polymorphic query: find basket connected to provision via ANY
        # sub-relation of provision_has_extracted_entity
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f'''
                match
                    $prov isa provision, has provision_id "{provision_id}";
                    $b isa {basket_type}, has basket_id $bid;
                    $rel isa provision_has_extracted_entity, links ($prov, $b);
                select $bid;
            '''
            rows = list(tx.query(query).resolve().as_concept_rows())
            if rows:
                bid = rows[0].get("bid").as_attribute().get_value()
                logger.debug(f"Resolved {basket_type} -> {bid}")
                return bid

            logger.warning(f"No {basket_type} found on provision {provision_id}")
            return None
        except Exception as e:
            logger.warning(f"Error resolving {basket_type}: {e}")
            return None
        finally:
            tx.close()

    def _ensure_cross_provision(self, deal_id: str, provision_id: str, provision_type: str):
        """Create a cross-covenant provision if it doesn't exist.

        Uses put for the entity (verified in TypeDB 3.x).
        Uses match-not-insert for the relation (put for relations is unverified).
        """
        try:
            # Ensure the provision entity exists (put is safe for entities)
            self._execute_query(f'''
                match
                    $deal isa deal, has deal_id "{deal_id}";
                put $prov isa {provision_type},
                    has provision_id "{provision_id}";
            ''')

            # Ensure the deal_has_provision relation exists
            self._execute_query(f'''
                match
                    $deal isa deal, has deal_id "{deal_id}";
                    $prov isa {provision_type}, has provision_id "{provision_id}";
                    not {{ (deal: $deal, provision: $prov) isa deal_has_provision; }};
                insert
                    (deal: $deal, provision: $prov) isa deal_has_provision;
            ''')
            logger.info(f"Ensured {provision_type}: {provision_id}")
        except Exception as e:
            logger.warning(f"Error ensuring cross-provision {provision_id}: {e}")

    def _ensure_cross_basket(self, provision_id: str, provision_type: str,
                             basket_type: str, basket_id: str, item: Dict):
        """Create a cross-covenant basket entity if it doesn't exist.

        Does NOT set section_reference from the reallocation item — the item's
        section_reference is where the reallocation cross-reference lives (e.g., 6.06(j)),
        not where the basket itself lives (e.g., 6.03(y)). The basket entity's
        section_reference will be populated when full investment covenant extraction is built.
        """
        try:
            attrs = [f'has basket_id "{basket_id}"']

            # Transfer dollar amount and grower from reallocation data
            amount = item.get("reallocation_amount_usd")
            if amount is not None:
                attrs.append(f'has basket_amount_usd {amount}')
            else:
                logger.warning(f"reallocation_amount_usd missing for {basket_type} — "
                             f"cross-covenant basket will lack basket_amount_usd")
            grower = item.get("reallocation_grower_pct")
            if grower is not None:
                attrs.append(f'has basket_grower_pct {grower}')
            # Intentionally NOT setting section_reference or source_text here.
            # Those describe the reallocation clause, not the basket's own location.

            attrs_str = ",\n                ".join(attrs)

            # Entity put (safe) + relation match-not-insert (separate calls)
            self._execute_query(f'''
                put $basket isa {basket_type},
                    {attrs_str};
            ''')

            self._execute_query(f'''
                match
                    $prov isa {provision_type}, has provision_id "{provision_id}";
                    $basket isa {basket_type}, has basket_id "{basket_id}";
                    not {{ (provision: $prov, basket: $basket) isa provision_has_basket; }};
                insert
                    (provision: $prov, basket: $basket) isa provision_has_basket;
            ''')
            logger.info(f"Ensured cross-covenant basket: {basket_type} ({basket_id})")

            # Set capacity_category from SSoT classification
            cap_cat = self._load_capacity_classifications().get(basket_type)
            if cap_cat:
                try:
                    self._execute_query(f'''
                        match $b isa {basket_type}, has basket_id "{basket_id}";
                            not {{ $b has capacity_category $existing; }};
                        insert $b has capacity_category "{cap_cat}";
                    ''')
                except Exception:
                    pass  # Already set or type doesn't support it
        except Exception as e:
            logger.warning(f"Error ensuring cross-basket {basket_type}: {e}")

    def _build_reallocation_edge_query(self, source_type: str, source_id: str,
                                        target_type: str, target_id: str,
                                        item: Dict) -> Optional[str]:
        """Build a TQL insert query for a basket_reallocates_to relation instance.

        SSoT: introspects basket_reallocates_to owned attributes from schema
        instead of using a hardcoded field map. Adding a new attribute to the
        relation in schema = it gets stored automatically.
        """
        try:
            source_key = self.get_key_attr_for_entity(source_type) or "basket_id"
            target_key = self.get_key_attr_for_entity(target_type) or "basket_id"

            # Introspect relation attributes from schema
            attr_types = self._get_relation_attr_types("basket_reallocates_to")

            rel_attrs = []
            for attr_name, vtype in attr_types.items():
                val = item.get(attr_name)
                if val is None:
                    continue
                if vtype == "boolean":
                    rel_attrs.append(f'has {attr_name} {str(val).lower()}')
                elif vtype in ("long", "double"):
                    rel_attrs.append(f'has {attr_name} {val}')
                elif vtype == "string":
                    rel_attrs.append(f'has {attr_name} "{self._escape(str(val)[:2000])}"')

            # capacity_effect is structural metadata, not extracted from the document.
            # "additive" = source's cap becomes additional capacity for the target.
            rel_attrs.append(f'has capacity_effect "additive"')

            rel_attrs_str = ""
            if rel_attrs:
                rel_attrs_str = ",\n                " + ",\n                ".join(rel_attrs)

            return f'''
                match
                    $source isa {source_type}, has {source_key} "{source_id}";
                    $target isa {target_type}, has {target_key} "{target_id}";
                insert
                    (source_basket: $source, target_basket: $target) isa basket_reallocates_to{rel_attrs_str};
            '''
        except Exception as e:
            logger.warning(f"Error building edge query: {e}")
            return None

    def store_extraction(
        self, deal_id: str, provision_id: str, response: ExtractionResponse
    ) -> Dict[str, Any]:
        """Store extraction results in two forms: flat answers and typed entities.

        Answers (provision_has_answer):
            Every answer gets a flat key-value record (question_id → typed value).
            Read by: frontend comparison grid, scalar Q&A.

        Entities (typed attributes):
            Scalar answers populate entity attributes via question_annotates_attribute.
            Multiselect answers set entity booleans via concept routing map.
            Entity_list answers create multi-instance entities.
            Read by: graph_reader → Claude synthesis, frontend entity_booleans endpoint.

        LEGACY (removing): concept_applicability relations.
            MFN still writes these — MFN concepts lack entity boolean routing.
            Once MFN concepts get target_entity_type / target_entity_attribute seed data,
            concept_applicability is deleted from the schema entirely.

        Processing order matters:
        1. _exists=True → create single-instance entities (must exist before attributes)
        2. entity_list → create multi-instance entities
        3. scalar → store flat answer + populate entity attributes via annotations
        4. multiselect → store flat answer + set entity booleans via concept routing
        """
        results = {
            "provision_id": provision_id,
            "entities_created": 0,
            "answers_stored": 0,
            "errors": [],
        }

        # Load routing tables (cached after first call)
        q_to_entity = self._load_question_to_entity_map()
        concept_routing = self._load_concept_routing_map()
        entity_list_types = self._load_entity_list_types()

        # Classify answers into processing groups
        exists_answers = []       # _exists=True for single-instance types
        entity_list_answers = []  # entity_list answers
        scalar_answers = []       # boolean/number/string (including _exists for flat storage)
        multiselect_answers = []  # multiselect arrays

        # Build set of known entity_list question IDs for validation
        known_el_qids = set()
        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            result = tx.query('''
                match $q isa ontology_question,
                    has question_id $qid,
                    has answer_type "entity_list";
                select $qid;
            ''').resolve()
            for row in result.as_concept_rows():
                known_el_qids.add(self._get_attr(row, "qid"))
        except Exception as e:
            logger.warning(f"Could not load entity_list question IDs: {e}")
        finally:
            if tx.is_open():
                tx.close()

        for answer in response.answers:
            if answer.value is None:
                continue
            if answer.answer_type == "entity_list":
                if answer.question_id in known_el_qids:
                    entity_list_answers.append(answer)
                else:
                    # Claude misclassified — reroute as multiselect if list, else scalar
                    logger.warning(
                        f"{answer.question_id}: Claude returned answer_type='entity_list' "
                        f"but question is not entity_list — reclassifying"
                    )
                    if isinstance(answer.value, list):
                        answer.answer_type = "multiselect"
                        multiselect_answers.append(answer)
                    else:
                        scalar_answers.append(answer)
                    continue
            elif answer.answer_type == "multiselect":
                multiselect_answers.append(answer)
            else:
                routing = q_to_entity.get(answer.question_id)
                if routing and routing[1] == "_exists" and answer.value is True:
                    if routing[0] not in entity_list_types and routing[0] not in cls._PROVISION_TYPES:
                        exists_answers.append(answer)
                scalar_answers.append(answer)

        logger.info(
            f"Classified {len(response.answers)} answers: "
            f"{len(exists_answers)} _exists, {len(entity_list_answers)} entity_list, "
            f"{len(scalar_answers)} scalar, {len(multiselect_answers)} multiselect"
        )

        # Phase 1: Create single-instance entities from _exists=True
        for answer in exists_answers:
            try:
                entity_type = q_to_entity[answer.question_id][0]
                # Use explicit section_reference from extraction, fall back to regex
                section_ref = answer.section_reference
                if not section_ref and answer.source_text:
                    section_ref = self._extract_section_ref(answer.source_text)
                self._create_single_instance_entity(
                    provision_id, entity_type,
                    source_text=answer.source_text,
                    source_page=answer.source_page,
                    section_reference=section_ref,
                )
                results["entities_created"] += 1
            except Exception as e:
                et = q_to_entity.get(answer.question_id, ("?",))[0]
                results["errors"].append(f"create_{et}: {str(e)[:100]}")

        # Phase 2: Create multi-instance entities from entity_list
        # (reallocations are stored as relations, not entities — handled inside _store_entity_list)
        for answer in entity_list_answers:
            try:
                count = self._store_entity_list(provision_id, answer, deal_id=deal_id)
                results["entities_created"] += count
            except Exception as e:
                results["errors"].append(f"{answer.question_id}: {str(e)[:100]}")

        # Phase 3: Store scalar answers (flat + entity attribute if annotated)
        for answer in scalar_answers:
            try:
                self._store_flat_answer(provision_id, answer)
                results["answers_stored"] += 1
                # Also populate entity attribute if annotation exists & single-instance type
                routing = q_to_entity.get(answer.question_id)
                if routing:
                    entity_type, attr_name = routing
                    if attr_name not in ("_exists", "_entity_list") and entity_type not in entity_list_types:
                        self._set_entity_attribute(provision_id, entity_type, attr_name, answer.value)
            except Exception as e:
                results["errors"].append(f"{answer.question_id}: {str(e)[:100]}")

        # Phase 4: Store multiselect answers (flat + entity booleans via concept routing)
        for answer in multiselect_answers:
            try:
                self._store_flat_answer(provision_id, answer)
                results["answers_stored"] += 1
                if isinstance(answer.value, list):
                    for concept_id in answer.value:
                        routings = concept_routing.get(concept_id, [])
                        for entity_type, attr_name in routings:
                            self._set_entity_attribute(provision_id, entity_type, attr_name, True)
            except Exception as e:
                results["errors"].append(f"{answer.question_id}: {str(e)[:100]}")

        logger.info(
            f"Extraction stored for {deal_id}: "
            f"{results['entities_created']} entities, "
            f"{results['answers_stored']} answers"
        )
        if results["errors"]:
            logger.warning(f"Storage errors: {results['errors'][:5]}")
        return results

    def _store_entity_list(self, provision_id: str, answer: Answer,
                           deal_id: str = None) -> int:
        """Store an entity_list answer — create entities + relations.

        Returns count of entities created.
        """
        if not isinstance(answer.value, list):
            logger.warning(f"{answer.question_id}: entity_list value is not a list")
            return 0

        # Special handling: reallocation data stored as relations, not entities
        if answer.question_id == "rp_el_reallocations" and deal_id:
            try:
                self.wire_reallocation_edges(deal_id, provision_id, answer.value)
                return len(answer.value)
            except Exception as e:
                logger.error(f"Reallocation relation storage failed: {e}")
                return 0

        # Load question metadata from TypeDB
        q_meta = self._load_entity_list_question_meta(answer.question_id)
        if not q_meta:
            logger.error(f"No metadata found for entity_list question {answer.question_id}")
            return 0

        target_entity_type = q_meta["target_entity_type"]
        target_relation_type = q_meta["target_relation_type"]

        config = self._get_relation_config(target_relation_type, provision_id)
        if not config:
            logger.error(f"No relation config for {target_relation_type}")
            return 0

        count = 0
        for i, item in enumerate(answer.value):
            if not isinstance(item, dict):
                continue
            try:
                self._store_single_entity(
                    provision_id, target_entity_type, target_relation_type,
                    config, item, i
                )
                count += 1
            except Exception as e:
                logger.warning(f"Failed to store entity {answer.question_id}[{i}]: {e}")

        return count

    # Cache for entity_list question metadata
    _el_question_meta_cache: Dict[str, Dict] = {}

    def _load_entity_list_question_meta(self, question_id: str) -> Optional[Dict]:
        """Load target_entity_type and target_relation_type for an entity_list question."""
        if question_id in self._el_question_meta_cache:
            return self._el_question_meta_cache[question_id]

        tx = typedb_client.driver.transaction(settings.typedb_database, TransactionType.READ)
        try:
            query = f'''
                match
                    $q isa ontology_question,
                        has question_id "{question_id}",
                        has target_entity_type $tet,
                        has target_relation_type $trt;
                select $tet, $trt;
            '''
            result = list(tx.query(query).resolve().as_concept_rows())
            if result:
                meta = {
                    "target_entity_type": self._get_attr(result[0], "tet"),
                    "target_relation_type": self._get_attr(result[0], "trt"),
                }
                self._el_question_meta_cache[question_id] = meta
                return meta
        except Exception as e:
            logger.error(f"Failed to load entity_list meta for {question_id}: {e}")
        finally:
            tx.close()
        return None

    def _store_single_entity(
        self,
        provision_id: str,
        target_entity_type: str,
        target_relation_type: str,
        config: Dict,
        item: Dict,
        index: int,
    ):
        """Store a single entity from an entity_list answer."""
        # Determine actual entity type (may be subtype for abstract types)
        actual_type = target_entity_type
        schema_info = self.get_entity_fields_from_schema(target_entity_type)

        if schema_info.get("is_abstract"):
            # Use "type" field from item to determine subtype
            declared_type = item.get("type", "")
            subtypes = schema_info.get("subtypes", {})
            if declared_type in subtypes:
                actual_type = declared_type
            else:
                # Try matching without suffix
                for sub_name in subtypes:
                    if declared_type in sub_name or sub_name.startswith(declared_type):
                        actual_type = sub_name
                        break
                else:
                    logger.warning(
                        f"Unknown subtype '{declared_type}' for {target_entity_type}, "
                        f"using first subtype"
                    )
                    if subtypes:
                        actual_type = next(iter(subtypes))

        # Get @key attribute for the actual entity type
        key_attr = self.get_key_attr_for_entity(actual_type)
        if not key_attr:
            # Fall back to parent type key
            key_attr = self.get_key_attr_for_entity(target_entity_type)
        if not key_attr:
            logger.error(f"No @key found for {actual_type}")
            return

        # Generate entity ID
        entity_id = f"{provision_id}_{actual_type}_{index}"

        # Build attribute list
        attrs = [f'has {key_attr} "{entity_id}"']

        # Get allowed fields from schema
        if schema_info.get("is_abstract") and actual_type in schema_info.get("subtypes", {}):
            sub_info = schema_info["subtypes"][actual_type]
            allowed_fields = set(sub_info.get("fields", []))
            # Also include common fields from parent
            allowed_fields |= set(schema_info.get("common_fields", []))
        else:
            allowed_fields = set(schema_info.get("fields", []))

        # Add provenance attrs to allowed set
        allowed_fields |= self._load_provenance_attrs()

        # Get expected value types from schema for type coercion
        attr_types = self.get_attr_value_types(actual_type)
        if not attr_types:
            logger.error(f"No schema type info for {actual_type} — cannot store entity")
            return
        if index == 0:
            logger.info(f"Type map for {actual_type}: {len(attr_types)} attrs — {attr_types}")

        for field_name, value in item.items():
            if field_name in ("type", "capacity_category"):
                continue  # Discriminator / SSoT-only fields, not extracted
            if field_name not in allowed_fields:
                continue
            if value is None:
                continue

            formatted = self._format_tql_value(value, attr_types.get(field_name))
            if formatted is not None:
                attrs.append(f'has {field_name} {formatted}')

        logger.info(f"Entity {actual_type}[{index}] attrs sample: {attrs[:4]}")

        attrs_str = ",\n                ".join(attrs)

        # Build match clause (parent_match is pre-formatted by _get_relation_config)
        parent_match = config["parent_match"]
        roles = config["roles"]
        parent_var = config.get("parent_var", "$prov")

        query = f'''
            match
                {parent_match}
            insert
                $entity isa {actual_type},
                {attrs_str};
                ({roles[0]}: {parent_var}, {roles[1]}: $entity) isa {target_relation_type};
        '''
        self._execute_query(query)

        # Set capacity_category from SSoT classification
        cap_cat = self._load_capacity_classifications().get(actual_type)
        if cap_cat:
            try:
                self._execute_query(f'''
                    match $b isa {actual_type}, has {key_attr} "{entity_id}";
                        not {{ $b has capacity_category $existing; }};
                    insert $b has capacity_category "{cap_cat}";
                ''')
            except Exception:
                pass  # Already set or type doesn't support it

    def _store_flat_answer(self, provision_id: str, answer: Answer):
        """Store a scalar or multiselect answer via store_scalar_answer.

        Multiselect answers are stored as flat scalar answers (no concept_applicability).
        """
        if answer.answer_type == "multiselect" and isinstance(answer.value, list):
            # Store multiselect as comma-separated string
            coerced = ", ".join(str(v) for v in answer.value)
        else:
            # Look up storage_value_type from TypeDB (SSoT); fall back to answer_type
            svt = self._get_storage_value_type(answer.question_id)
            coerced = self._coerce_flat_answer(answer.value, svt or answer.answer_type)

        if coerced is not None:
            self.store_scalar_answer(
                provision_id=provision_id,
                question_id=answer.question_id,
                value=coerced,
                source_text=answer.source_text,
                source_page=answer.source_page,
                source_section=answer.section_reference,
            )

    def _create_rp_provision_v4(self, provision_id: str):
        """Create RP provision and link to deal."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

        query = f'''
            match
                $deal isa deal, has deal_id "{self.deal_id}";
            insert
                $prov isa rp_provision,
                    has provision_id "{provision_id}",
                    has extracted_at {now_iso};
                (deal: $deal, provision: $prov) isa deal_has_provision;
        '''
        self._execute_query(query)
        logger.debug(f"Created rp_provision {provision_id}")

    def _ensure_rp_provision_v4(self, provision_id: str):
        """Create RP provision if it doesn't already exist. Idempotent.

        On re-extraction (provision exists), cleans up old answers/entities
        so fresh data can be stored without duplicates.
        """
        # Check if provision exists
        tx = self.driver.transaction(self.db_name, TransactionType.READ)
        try:
            result = list(tx.query(f'''
                match $p isa rp_provision, has provision_id "{provision_id}";
                select $p;
            ''').resolve().as_concept_rows())
            exists = len(result) > 0
        finally:
            tx.close()

        if exists:
            logger.info(f"Provision {provision_id} exists — cleaning up old data for re-extraction")
            self._cleanup_provision_data(provision_id)
            return

        # Create new provision + link to deal
        self._create_rp_provision_v4(provision_id)

    def _cleanup_provision_data(self, provision_id: str, deal_id: str = None):
        """Delete old answers, applicabilities, and entity relations for a provision.

        Preserves the provision entity and deal_has_provision link.
        Called before re-extraction to ensure clean state.

        Strategy: Delete entities directly (TypeDB cascades relation deletion).
        Work bottom-up: leaf entities first, then parent entities.
        If cascade fails, fall back to ID-pattern matching for orphaned entities.
        """
        deal_id = deal_id or self.deal_id
        pid = provision_id  # shorthand for pattern matching
        investment_prov_id = f"{deal_id}_investment"

        # Clear caches so they're reloaded after cleanup
        GraphStorage._cross_covenant_cache = None
        GraphStorage._capacity_class_cache = None

        cleanup_queries = [
            # ── Phase 1: Delete Channel 1 & 2 relations ─────────────────────
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_answer, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa concept_applicability, links (provision: $p);
            delete $rel;''',

            # ── Phase 2: Delete leaf entities (sources, exceptions) ──────────
            # Blocker exceptions — match through provision → blocker → exception
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, blocker: $b) isa provision_has_blocker;
                (blocker: $b, exception: $exc) isa blocker_has_exception;
            delete $exc;''',
            # Builder basket sources — match through provision → basket → source
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, basket: $b) isa provision_has_basket;
                (basket: $b, source: $src) isa basket_has_source;
            delete $src;''',

            # ── Phase 2b: Clean up investment provision data for this deal ──
            f'''match
                $prov isa investment_provision, has provision_id "{investment_prov_id}";
                (provision: $prov, basket: $b) isa provision_has_basket;
                $rel isa basket_reallocates_to, links (source_basket: $b);
            delete $rel;''',
            f'''match
                $prov isa investment_provision, has provision_id "{investment_prov_id}";
                (provision: $prov, basket: $b) isa provision_has_basket;
                $rel isa basket_reallocates_to, links (target_basket: $b);
            delete $rel;''',
            # Delete investment baskets
            f'''match
                $prov isa investment_provision, has provision_id "{investment_prov_id}";
                (provision: $prov, basket: $b) isa provision_has_basket;
            delete $b;''',
            # Delete investment provision
            f'''match
                $prov isa investment_provision, has provision_id "{investment_prov_id}";
            delete $prov;''',

            # ── Phase 3: Delete basket_reallocates_to relations ──────────────
            # RP baskets as source/target
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, basket: $b) isa provision_has_basket;
                $rel isa basket_reallocates_to, links (source_basket: $b);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, basket: $b) isa provision_has_basket;
                $rel isa basket_reallocates_to, links (target_basket: $b);
            delete $rel;''',
            # RDP baskets as source/target
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, rdp_basket: $b) isa provision_has_rdp_basket;
                $rel isa basket_reallocates_to, links (source_basket: $b);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, rdp_basket: $b) isa provision_has_rdp_basket;
                $rel isa basket_reallocates_to, links (target_basket: $b);
            delete $rel;''',

            # ── Phase 4: Delete mid-level entities (cascades their relations) ─
            # RP baskets — delete entity (cascades provision_has_basket)
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, basket: $b) isa provision_has_basket;
            delete $b;''',
            # RDP baskets
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, rdp_basket: $b) isa provision_has_rdp_basket;
            delete $b;''',
            # J.Crew blocker
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, blocker: $b) isa provision_has_blocker;
            delete $b;''',
            # Unsub designation
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, designation: $u) isa provision_has_unsub;
            delete $u;''',
            # Sweep tiers
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, tier: $t) isa provision_has_sweep_tier;
            delete $t;''',
            # De minimis thresholds
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, threshold: $t) isa provision_has_de_minimis;
            delete $t;''',
            # Basket reallocations
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, reallocation: $r) isa provision_has_reallocation;
            delete $r;''',
            # Investment pathways
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, pathway: $pw) isa provision_has_pathway;
            delete $pw;''',
            # Lien release mechanics (Phase 1)
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, lien_release: $lr) isa provision_has_lien_release;
            delete $lr;''',
            # Intercompany dividend permission (Phase 1)
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, permission: $perm) isa provision_has_intercompany_permission;
            delete $perm;''',
            # Definition analysis subtypes (Phase 1)
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, definition: $def) isa provision_has_definition;
            delete $def;''',
            # Sweep exemptions
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                (provision: $p, exemption: $ex) isa provision_has_sweep_exemption;
            delete $ex;''',

            # ── Phase 5: Fallback — delete orphaned entities by ID pattern ───
            # If cascade didn't delete relations, entities survive Phase 4.
            # Clean up remaining relations first, then retry entity deletion.
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_basket, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_rdp_basket, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_blocker, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_unsub, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_sweep_tier, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_de_minimis, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_reallocation, links (provision: $p);
            delete $rel;''',
            f'''match
                $p isa rp_provision, has provision_id "{provision_id}";
                $rel isa provision_has_pathway, links (provision: $p);
            delete $rel;''',
            # Delete orphaned entities by ID pattern
            f'''match $b isa rp_basket, has basket_id $bid; $bid like ".*_{pid}";
            delete $b;''',
            f'''match $b isa rdp_basket, has basket_id $bid; $bid like ".*_{pid}";
            delete $b;''',
            f'''match $s isa builder_basket_source, has source_id $sid; $sid like ".*_{pid}_.*";
            delete $s;''',
            f'''match $b isa jcrew_blocker, has blocker_id "jcrew_{pid}";
            delete $b;''',
            f'''match $e isa blocker_exception, has exception_id $eid; $eid like "jcrew_{pid}_.*";
            delete $e;''',
            f'''match $u isa unsub_designation, has designation_id "unsub_{pid}";
            delete $u;''',
            f'''match $t isa sweep_tier, has tier_id $tid; $tid like "sweep_{pid}_.*";
            delete $t;''',
            f'''match $t isa de_minimis_threshold, has threshold_id $tid; $tid like "deminimis_{pid}_.*";
            delete $t;''',
            f'''match $r isa basket_reallocation, has reallocation_id $rid; $rid like "realloc_{pid}_.*";
            delete $r;''',
            f'''match $pw isa investment_pathway, has pathway_id $pid2; $pid2 like "pathway_{pid}_.*";
            delete $pw;''',

            # ── Phase 6: Delete investment provision + baskets (separate provision) ─
            f'''match
                $inv_prov isa investment_provision, has provision_id "investment_{self.deal_id}";
                (provision: $inv_prov, basket: $b) isa provision_has_basket;
            delete $b;''',
            f'''match
                $inv_prov isa investment_provision, has provision_id "investment_{self.deal_id}";
            delete $inv_prov;''',
        ]

        for query in cleanup_queries:
            try:
                self._execute_query(query)
            except Exception:
                pass  # Silently skip if relation/entity doesn't exist

        logger.info(f"Cleaned up old data for provision {provision_id}")

    @staticmethod
    def _coerce_flat_answer(value: Any, storage_value_type: Optional[str]) -> Any:
        """Coerce a FlatAnswer value to the correct Python type based on storage_value_type.

        storage_value_type comes from TypeDB (ontology_question.storage_value_type).
        Values: "double", "boolean", "string", "integer".
        Falls back to "number"/"boolean" for legacy answer_type compatibility.
        """
        if value is None:
            return None
        if isinstance(value, str) and value.lower() in ("not_found", "n/a", "none", "null"):
            return None

        if storage_value_type in ("boolean",):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "yes", "1")
            return bool(value)

        if storage_value_type in ("double", "number"):
            try:
                return float(str(value).strip("'\"$%,"))
            except (ValueError, TypeError):
                return None

        if storage_value_type == "integer":
            try:
                return int(float(str(value).strip("'\"$%,")))
            except (ValueError, TypeError):
                return None

        # Default to string (includes storage_value_type=None or "string")
        return str(value)

    def store_scalar_answer(
        self,
        provision_id: str,
        question_id: str,
        value: Any,
        *,
        source_text: Optional[str] = None,
        source_page: Optional[int] = None,
        source_section: Optional[str] = None,
        confidence: Optional[str] = None,
    ) -> str:
        """
        Store a scalar answer via the provision_has_answer relation.

        Creates a provision_has_answer relation linking a provision to an
        ontology_question, with the answer value in the appropriate typed field.

        Args:
            provision_id: The provision to link the answer to
            question_id: The ontology_question question_id (e.g., "rp_m1")
            value: The answer value (bool, int, float, str)
            source_text: Optional verbatim source text
            source_page: Optional page number
            source_section: Optional section reference
            confidence: Optional confidence level (high | medium | low)

        Returns:
            The generated answer_id
        """
        answer_id = self._gen_id("ans")

        attrs = [f'has answer_id "{answer_id}"']

        # Use storage_value_type from TypeDB (SSoT) for routing;
        # fall back to isinstance checks for backwards compatibility
        svt = self._get_storage_value_type(question_id)
        if svt == "boolean":
            attrs.append(f'has answer_boolean {str(bool(value)).lower()}')
        elif svt == "double":
            attrs.append(f'has answer_double {float(value)}')
        elif svt == "integer":
            attrs.append(f'has answer_integer {int(value)}')
        elif svt == "string":
            attrs.append(f'has answer_string "{self._escape(str(value))}"')
        elif isinstance(value, bool):
            attrs.append(f'has answer_boolean {str(value).lower()}')
        elif isinstance(value, int):
            attrs.append(f'has answer_integer {value}')
        elif isinstance(value, float):
            attrs.append(f'has answer_double {value}')
        elif isinstance(value, str):
            attrs.append(f'has answer_string "{self._escape(value)}"')
        else:
            attrs.append(f'has answer_string "{self._escape(str(value))}"')

        if source_text:
            attrs.append(f'has source_text "{self._escape(source_text[:2000])}"')
        if source_page is not None:
            attrs.append(f'has source_page {source_page}')
        if source_section:
            attrs.append(f'has source_section "{self._escape(source_section)}"')
        if confidence:
            attrs.append(f'has confidence "{confidence}"')

        attrs_str = ",\n                ".join(attrs)

        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $q isa ontology_question, has question_id "{question_id}";
            insert
                (provision: $prov, question: $q) isa provision_has_answer,
                {attrs_str};
        '''
        self._execute_query(query)
        logger.debug(f"Stored answer {answer_id}: {question_id} = {value}")
        return answer_id

    # ═══════════════════════════════════════════════════════════════════════════
    # LEGACY ENTITY STORE METHODS — DELETED (Phase 2d-ii)
    # All entity storage now goes through _store_entity_list() / _store_single_entity()
    # via schema introspection. Old methods:
    #   _store_builder_basket_v4, _store_builder_source_v4, _store_ratio_basket_v4,
    #   _store_general_rp_basket_v4, _store_management_basket_v4, _store_tax_basket_v4,
    #   _store_holdco_overhead_basket_v4, _store_equity_award_basket_v4,
    #   _store_unsub_distribution_basket_v4, _store_refinancing_rdp_basket_v4,
    #   _store_general_rdp_basket_v4, _store_ratio_rdp_basket_v4,
    #   _store_builder_rdp_basket_v4, _store_equity_funded_rdp_basket_v4,
    #   _store_investment_pathway_v4, _store_jcrew_blocker_v4,
    #   _store_blocker_exception_v4, _store_unsub_designation_v4,
    #   _store_sweep_tier_v4, _store_de_minimis_v4, _store_reallocation_v4,
    #   _store_concept_applicability_v4, _link_blocker_to_ip_type,
    #   summarize_extraction, _BUILDER_SOURCE_TYPE_MAP, _BASKET_TYPE_MAP,
    #   _BLOCKER_EXCEPTION_TYPE_MAP, _load_source_subtype_attrs
    # ═══════════════════════════════════════════════════════════════════════════

    # Legacy MFN entity storage methods removed in Prompt 2.
    # MFN entities now flow through unified entity_list pipeline:
    # build_entity_list_prompt → _store_entity_list → _store_single_entity

    # ═══════════════════════════════════════════════════════════════════════════
    # UTILITIES
    # ═══════════════════════════════════════════════════════════════════════════

    def _gen_id(self, prefix: str) -> str:
        """Generate a unique ID with prefix."""
        return f"{prefix}_{self.deal_id}_{uuid.uuid4().hex[:8]}"

    def _execute_query(self, query: str, tx_type: TransactionType = TransactionType.WRITE) -> Any:
        """Execute a TypeQL query."""
        tx = self.driver.transaction(self.db_name, tx_type)
        try:
            result = tx.query(query).resolve()
            if tx_type == TransactionType.WRITE:
                tx.commit()
            else:
                tx.close()
            return result
        except Exception:
            tx.close()
            raise

    def _link_exemption_to_provision(self, provision_id: str, exemption_id: str):
        """Link sweep exemption to provision."""
        query = f'''
            match
                $prov isa provision, has provision_id "{provision_id}";
                $ex isa sweep_exemption, has exemption_id "{exemption_id}";
            insert
                (provision: $prov, exemption: $ex) isa provision_has_sweep_exemption;
        '''
        self._execute_query(query)

    def _format_tql_value(self, value, schema_type: Optional[str] = None) -> Optional[str]:
        """Format a Python value as a TypeQL literal, coercing to match schema type.

        If schema_type is known, coerces mismatches (e.g., bool→string, int→double).
        Returns the formatted string or None if the value can't be represented.
        """
        if value is None:
            return None

        # Schema-aware coercion
        if schema_type:
            st = schema_type.lower()
            if st == "string":
                # Coerce any type to string
                if isinstance(value, bool):
                    return f'"{str(value).lower()}"'
                return f'"{self._escape(str(value)[:2000])}"'
            elif st == "boolean":
                if isinstance(value, str):
                    return value.lower() in ("true", "yes", "1") and "true" or "false"
                return str(bool(value)).lower()
            elif st in ("double", "long"):
                if isinstance(value, bool):
                    return str(int(value))
                if isinstance(value, str):
                    try:
                        value = float(value)
                    except ValueError:
                        return None
                if st == "double" and isinstance(value, int):
                    return str(float(value))
                return str(value)

        # No schema type — skip attribute (don't guess)
        return None

    def _escape(self, text: str) -> str:
        """Escape text for TypeQL string."""
        if not text:
            return ""
        return text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
