"""
Graph Storage Service - V4 Graph-Native Schema

Handles inserting extracted covenant data as entities and relations
instead of flat attributes.
"""
import json
import logging
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

    @classmethod
    def _load_provenance_attrs(cls) -> set:
        """Discover provenance attributes: those owned by ALL extracted entity types.

        Provenance = framework infrastructure (WHERE data came from).
        Domain = entity-specific (WHAT the data is).

        If an attribute appears on every entity type, it's provenance by definition.
        No hardcoded list needed — the intersection IS the definition.
        """
        if cls._provenance_attrs_cache is not None:
            return cls._provenance_attrs_cache

        driver = typedb_client.driver
        if not driver:
            # Fallback if no driver — return known provenance attrs
            cls._provenance_attrs_cache = {"section_reference", "source_page", "source_text", "confidence"}
            return cls._provenance_attrs_cache

        db_name = settings.typedb_database

        # Collect all concrete entity types that participate in extraction
        # These are types that own attributes beyond just an ID key
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

        # Remove rp_provision itself — it's the anchor, not an extracted entity
        entity_types.discard("rp_provision")

        if not entity_types:
            cls._provenance_attrs_cache = set()
            return cls._provenance_attrs_cache

        # For each entity type, get all owned attributes (expanding abstract types to subtypes)
        all_attr_sets = []
        for et in entity_types:
            schema_info = cls.get_entity_fields_from_schema(et)
            if schema_info.get("is_abstract"):
                # Use each subtype's fields + common fields
                for sub_name, sub_info in schema_info.get("subtypes", {}).items():
                    fields = set(sub_info.get("fields", []))
                    fields |= set(schema_info.get("common_fields", []))
                    all_attr_sets.append(fields)
            else:
                fields = set(schema_info.get("fields", []))
                all_attr_sets.append(fields)

        if not all_attr_sets:
            cls._provenance_attrs_cache = set()
            return cls._provenance_attrs_cache

        # Intersection = attributes that appear on every entity type
        provenance = set.intersection(*all_attr_sets)
        # Remove key attributes (*_id pattern) — handled separately
        provenance = {a for a in provenance if not a.endswith("_id")}

        cls._provenance_attrs_cache = provenance
        logger.info(f"Discovered provenance attrs ({len(provenance)}): {sorted(provenance)}")
        return provenance

    @classmethod
    def get_entity_fields_from_schema(cls, entity_type: str) -> Dict[str, Any]:
        """Query TypeDB SCHEMA transaction to discover entity attributes.

        Classification:
        1. @key attributes → skip (system IDs)
        2. Provenance attrs (schema intersection) → skip from field list (appended as standard fields)
        3. Everything else → extractable fields

        For abstract types: introspect subtypes and their additional attributes.
        Returns dict with is_abstract, common_fields, subtypes (if abstract).
        """
        if entity_type in cls._entity_fields_cache:
            return cls._entity_fields_cache[entity_type]

        driver = typedb_client.driver
        if not driver:
            logger.warning("No TypeDB driver for schema introspection")
            return {"is_abstract": False, "fields": [], "subtypes": {}}

        db_name = settings.typedb_database
        tx = driver.transaction(db_name, TransactionType.SCHEMA)
        try:
            result = cls._introspect_entity_type(tx, entity_type)
            cls._entity_fields_cache[entity_type] = result
            return result
        except Exception as e:
            logger.error(f"Schema introspection failed for {entity_type}: {e}")
            return {"is_abstract": False, "fields": [], "subtypes": {}}
        finally:
            if tx.is_open():
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

        # Extractable = all - key - provenance
        extractable = sorted(all_attrs - key_attrs - cls._load_provenance_attrs())

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
    def get_key_attr_for_entity(cls, entity_type: str) -> Optional[str]:
        """Get the @key attribute name for an entity type. Cached.

        TypeDB 3.x doesn't support querying @key annotations in match clauses.
        We query all owned attrs and identify the key by *_id suffix convention.
        """
        if entity_type in cls._key_attr_cache:
            return cls._key_attr_cache[entity_type]

        driver = typedb_client.driver
        if not driver:
            return None

        db_name = settings.typedb_database
        tx = driver.transaction(db_name, TransactionType.SCHEMA)
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
            if tx.is_open():
                tx.close()

        return None

    _attr_value_type_cache: Dict[str, Dict[str, str]] = {}

    @classmethod
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
        tx = driver.transaction(db_name, TransactionType.SCHEMA)
        try:
            query = f"""
                match $et label {entity_type}; $et owns $attr;
                select $attr;
            """
            for row in tx.query(query).resolve().as_concept_rows():
                attr_type = row.get("attr").as_attribute_type()
                attr_name = attr_type.get_label()
                try:
                    vt = attr_type.get_value_type()
                    result[attr_name] = str(vt).lower() if vt else "string"
                except Exception:
                    result[attr_name] = "string"
        except Exception as e:
            logger.error(f"Failed to get attr value types for {entity_type}: {e}")
        finally:
            if tx.is_open():
                tx.close()

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
      "confidence": "high",
      "source_text": "exact verbatim quote from document (max 500 chars)",
      "source_page": 145
    },
    {
      "question_id": "rp_el_sweep_tiers",
      "value": [
        {"leverage_threshold": 5.75, "sweep_percentage": 0.5, "is_highest_tier": true,
         "section_reference": "2.10(f)", "source_page": 80}
      ],
      "answer_type": "entity_list",
      "confidence": "high"
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
- confidence: "high" (explicit), "medium" (inferred), "low" (uncertain)
- For entity_list answers: include section_reference, source_page, source_text, confidence on EACH entity object
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
        prompt += "Each entity object should include the listed fields plus provenance (section_reference, source_page, source_text, confidence).\n\n"

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
        prompt += 'For questions where the answer cannot be found in the document, respond with confidence: "not_found" and value: null.\n\n'

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
            section += f"- instructions: {hint}\n"

        # Introspect schema for fields
        schema_info = cls.get_entity_fields_from_schema(entity_type)

        if schema_info.get("is_abstract"):
            section += f"- **This is an abstract type with subtypes.** Include a `\"type\"` field to specify the subtype.\n"
            common = schema_info.get("common_fields", [])
            if common:
                section += f"- Common fields: {', '.join(common)}\n"
            for sub_name, sub_info in schema_info.get("subtypes", {}).items():
                sub_fields = sub_info.get("fields", [])
                if sub_fields:
                    section += f"  - **{sub_name}**: {', '.join(sub_fields)}\n"
        else:
            fields = schema_info.get("fields", [])
            if fields:
                section += f"- Fields: {', '.join(fields)}\n"

        section += f"- Provenance fields (include on each entity): section_reference, source_page, source_text, confidence\n"
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
                    confidence=raw.get("confidence", "high"),
                    source_text=raw.get("source_text", ""),
                    source_page=raw.get("source_page"),
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
    def _load_entity_list_types(cls) -> set:
        """Load entity types created by entity_list questions. Skip these for _exists creation.

        Expands abstract types to include subtypes (e.g. builder_basket_source → ecf_source, etc.).
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
            schema_info = cls.get_entity_fields_from_schema(et)
            if schema_info.get("is_abstract"):
                for sub in schema_info.get("subtypes", {}).keys():
                    expanded.add(sub)

        cls._entity_list_types_cache = expanded
        logger.info(f"Entity list types ({len(expanded)}): {sorted(expanded)}")
        return expanded

    @classmethod
    def _load_entity_relation_map(cls) -> Dict[str, tuple]:
        """Discover entity→provision relation from TypeDB schema introspection. Cached.

        For each single-instance entity type (from _exists annotations, minus entity_list types),
        queries the SCHEMA to find which relation links it to rp_provision, including
        inherited plays declarations.

        Returns: {entity_type: (relation_type, provision_role, entity_role)}
        """
        if cls._entity_relation_cache is not None:
            return cls._entity_relation_cache

        driver = typedb_client.driver
        if not driver:
            return {}

        q_to_entity = cls._load_question_to_entity_map()
        entity_list_types = cls._load_entity_list_types()

        # Collect entity types from _exists annotations, minus entity_list types and rp_provision
        target_types = set()
        for qid, (et, attr) in q_to_entity.items():
            if attr == "_exists" and et not in entity_list_types and et != "rp_provision":
                target_types.add(et)

        result = {}
        tx = driver.transaction(settings.typedb_database, TransactionType.SCHEMA)
        try:
            for et in sorted(target_types):
                query = f"""
                    match
                        $et1 label {et}; $et1 plays $role1;
                        $et2 label rp_provision; $et2 plays $role2;
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
                        logger.debug(f"Schema: {et} -> {rt} ({role2}, {role1})")
                    else:
                        logger.warning(f"No provision relation found for {et}")
                except Exception as e:
                    logger.warning(f"Schema introspection failed for {et}: {e}")
        finally:
            if tx.is_open():
                tx.close()

        cls._entity_relation_cache = result
        logger.info(f"Loaded entity->relation map for {len(result)} types: {sorted(result.keys())}")
        return result

    # ═══════════════════════════════════════════════════════════════════════════
    # SINGLE-INSTANCE ENTITY CREATION + ATTRIBUTE POPULATION
    # ═══════════════════════════════════════════════════════════════════════════

    def _create_single_instance_entity(self, provision_id: str, entity_type: str):
        """Create a single-instance entity and link to provision via schema-introspected relation.

        Called when an _exists answer is True (e.g., rp_k1=true → create jcrew_blocker).
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

        query = f'''
            match
                $prov isa rp_provision, has provision_id "{provision_id}";
            insert
                $entity isa {entity_type},
                    has {key_attr} "{entity_id}";
                ({prov_role}: $prov, {entity_role}: $entity) isa {relation_type};
        '''
        try:
            self._execute_query(query)
            logger.info(f"Created {entity_type}: {entity_id}")
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

    # Parent resolution: relation_type → (parent_match_template, role_names)
    # Used by _store_entity_list for multi-instance entities with complex parent chains
    _RELATION_CONFIG = {
        "provision_has_sweep_tier": {
            "parent_match": '$prov isa rp_provision, has provision_id "{provision_id}";',
            "roles": ("provision", "tier"),
        },
        "provision_has_de_minimis": {
            "parent_match": '$prov isa rp_provision, has provision_id "{provision_id}";',
            "roles": ("provision", "threshold"),
        },
        "provision_has_pathway": {
            "parent_match": '$prov isa rp_provision, has provision_id "{provision_id}";',
            "roles": ("provision", "pathway"),
        },
        "provision_has_reallocation": {
            "parent_match": '$prov isa rp_provision, has provision_id "{provision_id}";',
            "roles": ("provision", "reallocation"),
        },
        "provision_has_rdp_basket": {
            "parent_match": '$prov isa rp_provision, has provision_id "{provision_id}";',
            "roles": ("provision", "rdp_basket"),
        },
        "provision_has_sweep_exemption": {
            "parent_match": '$prov isa rp_provision, has provision_id "{provision_id}";',
            "roles": ("provision", "exemption"),
        },
        "has_amendment_threshold": {
            "parent_match": '$prov isa rp_provision, has provision_id "{provision_id}";',
            "roles": ("entity_with_threshold", "threshold"),
        },
        "blocker_has_exception": {
            "parent_match": (
                '$prov isa rp_provision, has provision_id "{provision_id}";'
                '\n                (provision: $prov, blocker: $parent) isa provision_has_blocker;'
            ),
            "roles": ("blocker", "exception"),
            "parent_var": "$parent",
        },
        "basket_has_source": {
            "parent_match": (
                '$prov isa rp_provision, has provision_id "{provision_id}";'
                '\n                (provision: $prov, basket: $parent) isa provision_has_basket;'
                '\n                $parent isa builder_basket;'
            ),
            "roles": ("basket", "source"),
            "parent_var": "$parent",
        },
    }

    def store_extraction(
        self, deal_id: str, provision_id: str, response: ExtractionResponse
    ) -> Dict[str, Any]:
        """
        Store extraction response with ordered processing.

        Processing order matters:
        1. _exists=True → create single-instance entities (must exist before attributes)
        2. entity_list → create multi-instance entities (Phase 2d-ii)
        3. scalar → store flat answer + populate entity attributes via annotations
        4. multiselect → store flat answer + set entity booleans via concept routing

        Args:
            deal_id: The deal ID
            provision_id: The provision ID (already ensured to exist)
            response: Parsed ExtractionResponse

        Returns:
            Dict with counts of stored items
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

        for answer in response.answers:
            if answer.value is None:
                continue
            if answer.answer_type == "entity_list":
                entity_list_answers.append(answer)
            elif answer.answer_type == "multiselect":
                multiselect_answers.append(answer)
            else:
                routing = q_to_entity.get(answer.question_id)
                if routing and routing[1] == "_exists" and answer.value is True:
                    if routing[0] not in entity_list_types and routing[0] != "rp_provision":
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
                self._create_single_instance_entity(provision_id, entity_type)
                results["entities_created"] += 1
            except Exception as e:
                et = q_to_entity.get(answer.question_id, ("?",))[0]
                results["errors"].append(f"create_{et}: {str(e)[:100]}")

        # Phase 2: Create multi-instance entities from entity_list
        for answer in entity_list_answers:
            try:
                count = self._store_entity_list(provision_id, answer)
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

    def _store_entity_list(self, provision_id: str, answer: Answer) -> int:
        """Store an entity_list answer — create entities + relations.

        Returns count of entities created.
        """
        if not isinstance(answer.value, list):
            logger.warning(f"{answer.question_id}: entity_list value is not a list")
            return 0

        # Load question metadata from TypeDB
        q_meta = self._load_entity_list_question_meta(answer.question_id)
        if not q_meta:
            logger.error(f"No metadata found for entity_list question {answer.question_id}")
            return 0

        target_entity_type = q_meta["target_entity_type"]
        target_relation_type = q_meta["target_relation_type"]

        config = self._RELATION_CONFIG.get(target_relation_type)
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

        tx = self.driver.transaction(self.db_name, TransactionType.READ)
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

        for field_name, value in item.items():
            if field_name == "type":
                continue  # Discriminator, not an attribute
            if field_name not in allowed_fields:
                continue
            if value is None:
                continue

            formatted = self._format_tql_value(value, attr_types.get(field_name))
            if formatted is not None:
                attrs.append(f'has {field_name} {formatted}')

        attrs_str = ",\n                ".join(attrs)

        # Build match clause
        parent_match = config["parent_match"].format(provision_id=provision_id)
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

    def _store_flat_answer(self, provision_id: str, answer: Answer):
        """Store a scalar or multiselect answer via store_scalar_answer.

        Multiselect answers are stored as flat scalar answers (no concept_applicability).
        """
        if answer.answer_type == "multiselect" and isinstance(answer.value, list):
            # Store multiselect as comma-separated string
            coerced = ", ".join(str(v) for v in answer.value)
        else:
            coerced = self._coerce_flat_answer(answer.value, answer.answer_type)

        if coerced is not None:
            self.store_scalar_answer(
                provision_id=provision_id,
                question_id=answer.question_id,
                value=coerced,
                source_text=answer.source_text,
                source_page=answer.source_page,
                confidence=answer.confidence,
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

    def _cleanup_provision_data(self, provision_id: str):
        """Delete old answers, applicabilities, and entity relations for a provision.

        Preserves the provision entity and deal_has_provision link.
        Called before re-extraction to ensure clean state.

        Strategy: Delete entities directly (TypeDB cascades relation deletion).
        Work bottom-up: leaf entities first, then parent entities.
        If cascade fails, fall back to ID-pattern matching for orphaned entities.
        """
        pid = provision_id  # shorthand for pattern matching

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
    def _coerce_flat_answer(value: Any, answer_type: str) -> Any:
        """Coerce a FlatAnswer value to the correct Python type for store_scalar_answer."""
        if value is None:
            return None
        if isinstance(value, str) and value.lower() in ("not_found", "n/a", "none", "null"):
            return None

        if answer_type == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.lower() in ("true", "yes", "1")
            return bool(value)

        if answer_type == "number":
            try:
                v = float(str(value).strip("'\""))
                return int(v) if v == int(v) else v
            except (ValueError, TypeError):
                return None

        # Default to string
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

        if isinstance(value, bool):
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

    # Placeholder so line references don't break catastrophically
    _LEGACY_DELETED = True

    # MFN ENTITY STORAGE (Channel 3)
    # ═══════════════════════════════════════════════════════════════════════════

    def store_mfn_exclusion(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_exclusion entity + provision_has_exclusion relation."""
        excl_id = f"mfn_excl_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has exclusion_id "{excl_id}"']

        field_map = {
            "exclusion_type": ("string", "exclusion_type"),
            "exclusion_has_cap": ("bool", "exclusion_has_cap"),
            "exclusion_cap_usd": ("double", "exclusion_cap_usd"),
            "exclusion_cap_pct_ebitda": ("double", "exclusion_cap_pct_ebitda"),
            "exclusion_conditions": ("string", "exclusion_conditions"),
            "can_stack_with_other_exclusions": ("bool", "can_stack_with_other_exclusions"),
            "excludes_from_mfn": ("bool", "excludes_from_mfn"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $excl isa mfn_exclusion,
                {attrs_str};
                (provision: $prov, exclusion: $excl) isa provision_has_exclusion;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_exclusion: {excl_id}")
        return excl_id

    def store_mfn_yield_definition(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_yield_definition entity + provision_has_yield_def relation."""
        yield_id = f"mfn_yield_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has yield_def_id "{yield_id}"']

        field_map = {
            "defined_term": ("string", "defined_term"),
            "includes_margin": ("bool", "includes_margin"),
            "includes_floor_benefit": ("bool", "includes_floor_benefit"),
            "includes_oid": ("bool", "includes_oid"),
            "includes_upfront_fees": ("bool", "includes_upfront_fees"),
            "includes_commitment_fees": ("bool", "includes_commitment_fees"),
            "includes_other_fees": ("bool", "includes_other_fees"),
            "oid_amortization_method": ("string", "oid_amortization_method"),
            "comparison_baseline": ("string", "comparison_baseline"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')
        if entity_data.get("oid_amortization_years") is not None:
            attrs.append(f'has oid_amortization_years {int(entity_data["oid_amortization_years"])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $ydef isa mfn_yield_definition,
                {attrs_str};
                (provision: $prov, yield_def: $ydef) isa provision_has_yield_def;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_yield_definition: {yield_id}")
        return yield_id

    def store_mfn_sunset_provision(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_sunset_provision entity + provision_has_sunset relation."""
        sunset_id = f"mfn_sunset_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has sunset_id "{sunset_id}"']

        field_map = {
            "sunset_exists": ("bool", "sunset_exists"),
            "sunset_trigger_event": ("string", "sunset_trigger_event"),
            "sunset_resets_on_refi": ("bool", "sunset_resets_on_refi"),
            "sunset_tied_to_maturity": ("bool", "sunset_tied_to_maturity"),
            "sunset_timing_loophole": ("bool", "sunset_timing_loophole"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')
        if entity_data.get("sunset_period_months") is not None:
            attrs.append(f'has sunset_period_months {int(entity_data["sunset_period_months"])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $sun isa mfn_sunset_provision,
                {attrs_str};
                (provision: $prov, sunset: $sun) isa provision_has_sunset;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_sunset_provision: {sunset_id}")
        return sunset_id

    def store_mfn_freebie_basket(self, provision_id: str, entity_data: dict) -> str:
        """Store mfn_freebie_basket entity + provision_has_freebie relation."""
        freebie_id = f"mfn_freebie_{self.deal_id}_{uuid.uuid4().hex[:8]}"
        attrs = [f'has freebie_id "{freebie_id}"']

        field_map = {
            "uses_greater_of": ("bool", "uses_greater_of"),
            "stacks_with_general_basket": ("bool", "stacks_with_general_basket"),
            "section_reference": ("string", "section_reference"),
            "source_text": ("string", "source_text"),
            "confidence": ("string", "confidence"),
        }
        attrs.extend(self._build_attrs_from_data(entity_data, field_map))

        if entity_data.get("source_page") is not None:
            attrs.append(f'has source_page {int(entity_data["source_page"])}')

        for dbl_field in ("dollar_amount_usd", "ebitda_pct",
                          "general_basket_amount_usd", "total_mfn_exempt_capacity_usd"):
            if entity_data.get(dbl_field) is not None:
                attrs.append(f'has {dbl_field} {float(entity_data[dbl_field])}')

        attrs_str = ",\n                ".join(attrs)
        query = f'''
            match
                $prov isa mfn_provision, has provision_id "{provision_id}";
            insert
                $fb isa mfn_freebie_basket,
                {attrs_str};
                (provision: $prov, freebie: $fb) isa provision_has_freebie;
        '''
        self._execute_query(query)
        logger.debug(f"Stored mfn_freebie_basket: {freebie_id}")
        return freebie_id

    def _build_attrs_from_data(self, data: dict, field_map: dict) -> list:
        """Build TypeQL attribute clauses from entity data dict.

        field_map: {json_key: (type, tql_attr_name)}
        type is 'string', 'bool', 'double', or 'int'.
        """
        attrs = []
        for json_key, (ftype, tql_name) in field_map.items():
            val = data.get(json_key)
            if val is None:
                continue
            if ftype == "string":
                attrs.append(f'has {tql_name} "{self._escape(str(val)[:2000])}"')
            elif ftype == "bool":
                attrs.append(f'has {tql_name} {str(val).lower()}')
            elif ftype == "double":
                attrs.append(f'has {tql_name} {float(val)}')
            elif ftype == "int":
                attrs.append(f'has {tql_name} {int(val)}')
        return attrs

    MFN_ENTITY_STORE_MAP = {
        "mfn_exclusion": "store_mfn_exclusion",
        "mfn_yield_definition": "store_mfn_yield_definition",
        "mfn_sunset_provision": "store_mfn_sunset_provision",
        "mfn_freebie_basket": "store_mfn_freebie_basket",
    }

    @classmethod
    def load_mfn_extraction_metadata(cls) -> list:
        """Load extraction metadata for MFN entity types only."""
        driver = typedb_client.driver
        db_name = settings.typedb_database

        if not driver:
            logger.warning("No TypeDB driver for MFN metadata")
            return []

        query = '''
            match
                $em isa extraction_metadata,
                    has metadata_id $id,
                    has target_entity_type $type,
                    has extraction_prompt $prompt;
                $id like "mfn_.*";
            select $id, $type, $prompt, $em;
        '''

        tx = driver.transaction(db_name, TransactionType.READ)
        try:
            result = tx.query(query).resolve()
            rows = list(result.as_concept_rows())
            tx.close()

            metadata = []
            for row in rows:
                item = {
                    "metadata_id": cls._get_attr(row, "id"),
                    "target_entity_type": cls._get_attr(row, "type"),
                    "extraction_prompt": cls._get_attr(row, "prompt"),
                }
                meta_id = item["metadata_id"]
                item.update(cls._get_optional_metadata_attrs(meta_id))
                metadata.append(item)

            metadata.sort(key=lambda x: x.get("extraction_priority", 99))
            return metadata

        except Exception as e:
            if tx.is_open():
                tx.close()
            logger.error(f"Error loading MFN extraction metadata: {e}")
            return []

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

        # No schema type — use Python type inference
        if isinstance(value, bool):
            return str(value).lower()
        elif isinstance(value, (int, float)):
            return str(value)
        elif isinstance(value, str):
            return f'"{self._escape(value[:2000])}"'
        return None

    def _escape(self, text: str) -> str:
        """Escape text for TypeQL string."""
        if not text:
            return ""
        return text.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '')
