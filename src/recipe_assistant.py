"""Deterministic candidate retrieval and ingredient-based refinement for recipe searches."""

import json
import re
import uuid
from collections import Counter
from typing import Any

from llm import ask
from prompts import load_prompt
from recipe_allergens import ALLERGEN_KEYWORDS, ALLERGEN_LABELS
from recipe_index import RecipeIndex


OPTION_COUNT = 6
FINAL_CHOICE_LIMIT = 3
STOPWORDS = frozenset(
    "a an the and or of with without no not free some something anything any "
    "i me my we want wants like would love need looking look for to in on at "
    "that this is it be please give show find make dessert desserts recipe "
    "recipes something most least maximum minimum max min fewer less more "
    "under over up people person guests dinner party gathering zero one two "
    "three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen twenty thirty forty fifty".split()
)
NUMBER_WORDS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    "thirty|forty|fifty"
)
GENERIC_EXCLUSION_RE = re.compile(
    r"\b(?:without|excluding|exclude|avoid(?:ing)?|no)\s+(?:any\s+)?([^\W\d_]+)",
    re.IGNORECASE,
)
QUANTITY_CRITERION_RE = re.compile(
    r"\b(?:at\s+most|no\s+more\s+than|up\s+to|maximum(?:\s+of)?|max(?:imum)?(?:\s+of)?|"
    r"less\s+than|fewer\s+than|under)\s+(?:\d+(?:\.\d+)?|" + NUMBER_WORDS + r")"
    r"(?:\s*(?:g|kg|mg|ml|l|cup|cups|tablespoons?|teaspoons?))?\s+"
    r"[^\W\d_]+(?:-[^\W\d_]+)?",
    re.IGNORECASE,
)
RESTART_MESSAGE = (
    "None of the offered ingredients narrowed the results enough. Please enter your "
    "request again with more selection criteria."
)
SEARCH_FILTER_PROMPT = load_prompt("search_filter_v1")
FILTER_BATCH_CHARS = 40000
MAX_FILTER_CANDIDATES = 12
SEARCH_QUERY_PROMPT = load_prompt("search_query_v1")

SEARCH_FIELD_DESCRIPTIONS = {
    "recipe_id": "Stable chunk ID",
    "source_path": "Source document and page reference",
    "page": "Source page number when available",
    "content": "Complete recipe text and preparation",
    "metadata.name": "Recipe name",
    "metadata.difficulty": "Recipe difficulty",
    "metadata.ingredients.name": "Ingredient names",
    "metadata.ingredients.quantity": "Ingredient quantities",
    "metadata.ingredients.recognition_status": "Ingredient recognition status",
    "metadata.ingredients.recognition_note": "Ingredient review notes",
    "metadata.allergens.name": "Listed allergen groups",
    "metadata.allergens.source_ingredients": "Ingredient names supporting allergens",
    "metadata.servings": "Recipe serving count or description",
    "metadata.utensils.name": "Utensils",
    "metadata.instructions": "Preparation instructions",
    "metadata.ingredient_status": "Overall ingredient recognition status",
    "metadata.allergen_status": "Overall allergen assessment status",
    "metadata.uncertainties": "Extraction uncertainties",
    "metadata.manual_edits": "Back-office edit history",
}


def _stem(token: str) -> str:
    token = token.casefold()
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _stems(text: str) -> set[str]:
    return {_stem(token) for token in re.findall(r"[^\W_]+", text, re.UNICODE)}


def _normalized_ingredient(name: str) -> str:
    name = re.sub(r"\([^)]*\)", " ", name.casefold()).split(",")[0]
    words = re.findall(r"[^\W\d_]+", name, re.UNICODE)
    return " ".join(words[:-1] + [_stem(words[-1])]) if words else ""


class RecipeAssistant:
    def __init__(self, index: RecipeIndex) -> None:
        self.index = index

    @classmethod
    def _no_match_message(cls, query: str) -> str:
        if cls._has_exclusions(query):
            return (
                "No suitable recipe was found with all ingredients recognized and without "
                "any excluded ingredient or listed allergen."
            )
        return "No recipe matching your request was found in the available records."

    @staticmethod
    def _has_exclusions(query: str) -> bool:
        constraints, _, excluded_words = RecipeAssistant._split_query(query)
        return bool(constraints or excluded_words)

    @staticmethod
    def _allergy_constraints(query: str) -> set[str]:
        return RecipeAssistant._parse_exclusions(query)[0]

    @staticmethod
    def _parse_exclusions(query: str) -> tuple[set[str], list[tuple[int, int]]]:
        constraints: set[str] = set()
        spans: list[tuple[int, int]] = []
        terms = sorted(
            {
                keyword
                for keywords in ALLERGEN_KEYWORDS.values()
                for keyword in keywords
            }
            | set(ALLERGEN_KEYWORDS)
            | set(ALLERGEN_LABELS)
            | {"nut", "nuts", "tree nuts", "shellfish", "dairy", "lactose"},
            key=len,
            reverse=True,
        )
        term_pattern = "|".join(re.escape(term) for term in terms)

        def add_constraint(raw_term: str) -> None:
            term = raw_term.casefold()
            if term == "shellfish":
                constraints.update({"crustaceans", "molluscs"})
            elif term in {"nut", "nuts", "tree nuts"}:
                constraints.add("tree nuts")
            elif term in {"dairy", "lactose"}:
                constraints.add("milk")
            else:
                for group, keywords in ALLERGEN_KEYWORDS.items():
                    if term in keywords or term == group.casefold():
                        constraints.add(group)
                canonical = ALLERGEN_LABELS.get(" ".join(term.split()))
                if canonical:
                    constraints.add(canonical)

        pattern = re.compile(
            r"\b(?:allerg(?:y|ic)\s+(?:to\s+)?|intoleran\w*\s+to\s+|"
            r"avoid(?:ing)?\s+|without\s+|free\s+of\s+|no\s+|exclude(?:d|ing)?\s+)"
            r"(?:(?:all|any)\s+)?(" + term_pattern + r")"
            r"((?:\s*(?:,|and|or)\s*(?:" + term_pattern + r"))*)\b",
            re.IGNORECASE,
        )
        for match in pattern.finditer(query):
            preceding = query[max(0, match.start() - 12):match.start()].casefold().strip()
            if preceding.endswith("not"):
                continue
            spans.append(match.span())
            add_constraint(match.group(1))
            for extra in re.finditer(r"(?:,|and|or)\s*(" + term_pattern + r")", match.group(2), re.I):
                add_constraint(extra.group(1))

        for match in re.finditer(
            r"\b(" + term_pattern + r")\s+allerg(?:y|ic)\b",
            query,
            re.IGNORECASE,
        ):
            spans.append(match.span())
            add_constraint(match.group(1))

        for match in re.finditer(
            r"\b(" + term_pattern + r")-free\b", query, re.IGNORECASE
        ):
            spans.append(match.span())
            add_constraint(match.group(1))
        return constraints, spans

    @staticmethod
    def _candidate_allergens(candidate: dict[str, Any]) -> set[str]:
        groups: set[str] = set()
        for allergen in candidate["metadata"].get("allergens", []):
            if isinstance(allergen, dict) and isinstance(allergen.get("name"), str):
                name = allergen["name"]
                canonical = ALLERGEN_LABELS.get(" ".join(name.casefold().split()))
                if canonical:
                    groups.add(canonical)
                for group, keywords in ALLERGEN_KEYWORDS.items():
                    if any(keyword in name.casefold() for keyword in keywords):
                        groups.add(group)
        return groups

    @staticmethod
    def _split_query(query: str) -> tuple[list[str], set[str], set[str]]:
        """Return positive search terms, excluded allergen groups and excluded ingredient words."""
        constraints, spans = RecipeAssistant._parse_exclusions(query)
        positive = list(query)
        for start, end in spans:
            positive[start:end] = " " * (end - start)
        remaining = "".join(positive)
        remaining = QUANTITY_CRITERION_RE.sub(" ", remaining)

        excluded_words: set[str] = set()
        for match in GENERIC_EXCLUSION_RE.finditer(remaining):
            excluded_words.add(_stem(match.group(1)))
        remaining = GENERIC_EXCLUSION_RE.sub(" ", remaining)

        terms: list[str] = []
        for token in re.findall(r"[^\W_]+", remaining.casefold(), re.UNICODE):
            if token in STOPWORDS or token.isdigit():
                continue
            stem = _stem(token)
            if stem not in terms:
                terms.append(stem)
        return terms, constraints, excluded_words

    @staticmethod
    def _ingredient_names(candidate: dict[str, Any]) -> set[str]:
        return {
            normalized
            for item in candidate["metadata"].get("ingredients", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
            for normalized in [_normalized_ingredient(item["name"])]
            if normalized
        }

    @classmethod
    def _ranked_ingredients(
        cls, candidates: list[dict[str, Any]], used_terms: set[str]
    ) -> list[str]:
        counts: Counter[str] = Counter()
        for item in candidates:
            counts.update(cls._ingredient_names(item))
        ranked = [
            (name, count)
            for name, count in counts.items()
            if count < len(candidates)
            and not used_terms & _stems(name)
        ]
        ranked.sort(key=lambda pair: (-pair[1], pair[0]))
        return [name for name, _ in ranked]

    @classmethod
    def _eligible_candidates(
        cls, query: str, candidates: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        constraints, _ = cls._parse_exclusions(query)
        _, _, excluded_words = cls._split_query(query)
        has_exclusions = bool(constraints or excluded_words)
        return [
            candidate
            for candidate in candidates
            if (
                not has_exclusions
                or candidate["metadata"].get("allergen_status") == "assessed"
                and all(
                    isinstance(item, dict)
                    and item.get("recognition_status") == "recognized"
                    for item in candidate["metadata"].get("ingredients", [])
                )
            )
            and not (cls._candidate_allergens(candidate) & constraints)
            and not excluded_words & _stems(
                " ".join(
                    str(item.get("name", ""))
                    for item in candidate["metadata"].get("ingredients", [])
                    if isinstance(item, dict)
                )
            )
        ]

    @staticmethod
    def _parse_filter_response(
        response: str,
        candidate_ids: set[str],
        preferences: list[str],
    ) -> tuple[list[str], dict[str, str] | None]:
        if not isinstance(response, str):
            raise RuntimeError("The LLM returned invalid recipe-filter results.")
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            result = json.loads(cleaned)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("The LLM returned invalid recipe-filter results.") from exc
        if not isinstance(result, dict):
            raise RuntimeError("The LLM returned invalid recipe-filter results.")
        ids = result.get("candidate_ids")
        if isinstance(ids, list) and all(isinstance(i, str) for i in ids):
            ids = list(dict.fromkeys(ids))
        recommendation = result.get("recommendation")
        if (
            set(result) != {"candidate_ids", "recommendation"}
            or
            not isinstance(ids, list)
            or any(not isinstance(recipe_id, str) or not recipe_id for recipe_id in ids)
            or len(ids) != len(set(ids))
            or not set(ids).issubset(candidate_ids)
        ):
            raise RuntimeError("The LLM returned invalid recipe-filter results.")
        if recommendation is None or not preferences:
            # Without a recommendation (or when no preference was requested, so
            # none is applicable) the hard-filtered candidates continue
            # through the deterministic refinement workflow.
            return ids, None
        if (
            not isinstance(recommendation, dict)
            or set(recommendation) != {"recipe_id", "reason"}
            or recommendation["recipe_id"] not in ids
            or not isinstance(recommendation["reason"], str)
            or not recommendation["reason"].strip()
            or len(recommendation["reason"]) > 500
        ):
            raise RuntimeError("The LLM returned invalid recipe-filter results.")
        return ids, {
            "recipe_id": recommendation["recipe_id"],
            "reason": recommendation["reason"].strip(),
        }

    @staticmethod
    def _servings_count(candidate: dict[str, Any]) -> int | None:
        value = candidate["metadata"].get("servings")
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        match = re.search(r"\d+", value) if isinstance(value, str) else None
        return int(match.group()) if match else None

    @classmethod
    def _locally_verifiable_reject(
        cls, candidate: dict[str, Any], constraint: dict[str, Any]
    ) -> bool:
        """Reject only on facts verifiable from structured metadata; unknowns go to the LLM."""
        field = constraint["field"]
        operator = constraint["operator"]
        value = constraint["value"]
        metadata = candidate["metadata"]
        if field == "metadata.servings" and operator in {
            "at_least", "greater_than", "at_most", "less_than", "equals",
        }:
            servings = cls._servings_count(candidate)
            number = re.match(r"\d+(?:\.\d+)?", str(value).strip())
            if servings is None or not number:
                return False
            limit = float(number.group())
            return not {
                "at_least": servings >= limit,
                "greater_than": servings > limit,
                "at_most": servings <= limit,
                "less_than": servings < limit,
                "equals": servings == limit,
            }[operator]
        wanted = _stems(str(value))
        if not wanted or operator not in {"exclude", "does_not_contain"}:
            return False
        if field == "metadata.ingredients.name" and operator == "does_not_contain":
            names = [
                item.get("name", "")
                for item in metadata.get("ingredients", [])
                if isinstance(item, dict)
            ]
            return any(wanted <= _stems(str(name)) for name in names)
        if field == "metadata.allergens.name" and operator == "exclude":
            for allergen in metadata.get("allergens", []):
                if not isinstance(allergen, dict):
                    continue
                texts = [allergen.get("name", "")] + list(allergen.get("source_ingredients", []))
                if any(wanted <= _stems(str(text)) for text in texts):
                    return True
        return False

    @classmethod
    def _prefilter_locally(
        cls, candidates: list[dict[str, Any]], criteria: dict[str, Any]
    ) -> list[dict[str, Any]]:
        constraints = [c for c in criteria["constraints"] if isinstance(c, dict)]
        return [
            candidate
            for candidate in candidates
            if not any(cls._locally_verifiable_reject(candidate, c) for c in constraints)
        ]

    @classmethod
    def _rank_candidates(
        cls, candidates: list[dict[str, Any]], criteria: dict[str, Any]
    ) -> list[dict[str, Any]]:
        terms = [_stems(term) for term in criteria["terms"]]
        terms = [term for term in terms if term]
        if not terms:
            return candidates

        def score(candidate: dict[str, Any]) -> float:
            names = [
                _stems(str(item.get("name", "")))
                for item in candidate["metadata"].get("ingredients", [])
                if isinstance(item, dict)
            ]
            total = 0.0
            for term in terms:
                tokens = set().union(
                    *(cls._criteria_values(candidate, f) for f in criteria["search_fields"])
                )
                if term <= tokens:
                    total += 1
                    # A single ingredient naming the whole term is the best evidence.
                    if any(term <= name for name in names):
                        total += 0.5
            return total

        return sorted(candidates, key=score, reverse=True)

    @staticmethod
    def _batch_candidates(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        batches: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        size = 0
        for candidate in candidates:
            candidate_size = len(json.dumps(candidate, ensure_ascii=False))
            if current and size + candidate_size > FILTER_BATCH_CHARS:
                batches.append(current)
                current, size = [], 0
            current.append(candidate)
            size += candidate_size
        if current:
            batches.append(current)
        return batches

    @classmethod
    def _filter_batch_with_llm(
        cls,
        query: str,
        candidates: list[dict[str, Any]],
        criteria: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        payload = {
            "user_request": query,
            "retrieval_criteria": criteria,
            "candidates": candidates,
        }
        try:
            response = ask(
                json.dumps(payload, ensure_ascii=False),
                system=SEARCH_FILTER_PROMPT,
            )
        except Exception as exc:
            raise RuntimeError("The LLM could not filter the retrieved recipes.") from exc
        ordered_ids, recommendation = cls._parse_filter_response(
            response,
            {candidate["recipe_id"] for candidate in candidates},
            criteria["preferences"],
        )
        by_id = {candidate["recipe_id"]: candidate for candidate in candidates}
        return [by_id[recipe_id] for recipe_id in ordered_ids], recommendation

    @classmethod
    def _filter_candidates_with_llm(
        cls,
        query: str,
        candidates: list[dict[str, Any]],
        criteria: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], dict[str, str] | None]:
        # Large retrievals are filtered in batches of complete chunks to stay
        # within the LLM request size limit.
        batches = cls._batch_candidates(candidates)
        if len(batches) == 1:
            return cls._filter_batch_with_llm(query, candidates, criteria)
        survivors: list[dict[str, Any]] = []
        for batch in batches:
            kept, _ = cls._filter_batch_with_llm(query, batch, criteria)
            survivors.extend(kept)
        if criteria["preferences"] and survivors:
            # Choose the recommendation among all hard-filtered survivors.
            return cls._filter_batch_with_llm(query, survivors, criteria)
        return survivors, None

    @staticmethod
    def _parse_query_criteria(
        response: str, available_fields: set[str]
    ) -> dict[str, Any]:
        if not isinstance(response, str):
            raise RuntimeError("The LLM returned invalid RAG search criteria.")
        cleaned = response.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            result = json.loads(cleaned)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("The LLM returned invalid RAG search criteria.") from exc
        if not isinstance(result, dict) or set(result) != {
            "search_fields", "terms", "match", "constraints", "preferences"
        }:
            raise RuntimeError("The LLM returned invalid RAG search criteria.")
        fields = result["search_fields"]
        terms = result["terms"]
        match = result["match"]
        constraints = result["constraints"]
        preferences = result["preferences"]
        if (
            not isinstance(fields, list)
            or any(not isinstance(field, str) or field not in available_fields for field in fields)
            or len(fields) != len(set(fields))
            or not isinstance(terms, list)
            or len(terms) > 20
            or any(
                not isinstance(term, str)
                or not term.strip()
                or len(term) > 100
                or not _stems(term)
                for term in terms
            )
            or (terms and not fields)
            or not isinstance(match, str)
            or match not in {"any", "all"}
            or not isinstance(constraints, list)
            or len(constraints) > 20
            or not isinstance(preferences, list)
            or len(preferences) > 10
            or any(
                not isinstance(preference, str)
                or not preference.strip()
                or len(preference) > 100
                for preference in preferences
            )
        ):
            raise RuntimeError("The LLM returned invalid RAG search criteria.")
        for constraint in constraints:
            if (
                isinstance(constraint, dict)
                and constraint.get("field") == "metadata.servings"
                and constraint.get("operator") in {"at_most", "less_than", "at_least", "greater_than"}
                and not constraint.get("subject")
            ):
                constraint["subject"] = "servings"
            field = constraint.get("field") if isinstance(constraint, dict) else None
            operator = constraint.get("operator") if isinstance(constraint, dict) else None
            if (
                not isinstance(constraint, dict)
                or set(constraint) - {"field", "operator", "value", "subject"}
                or not isinstance(field, str)
                or field not in available_fields
                or not isinstance(operator, str)
                or operator not in {
                    "exclude", "at_most", "less_than", "at_least",
                    "greater_than", "equals", "contains", "does_not_contain",
                }
                or isinstance(constraint.get("value"), bool)
                or not isinstance(constraint.get("value"), (str, int, float))
                or isinstance(constraint.get("value"), str)
                and not constraint["value"].strip()
                or ("subject" in constraint and not isinstance(constraint["subject"], str))
                or operator in {"at_most", "less_than", "at_least", "greater_than"}
                and (
                    not isinstance(constraint.get("subject"), str)
                    or not constraint["subject"].strip()
                    or not (
                        isinstance(constraint.get("value"), (int, float))
                        and not isinstance(constraint.get("value"), bool)
                        or isinstance(constraint.get("value"), str)
                        and re.fullmatch(
                            r"\d+(?:\.\d+)?\s*[a-zA-Z]*",
                            constraint["value"].strip(),
                        )
                    )
                )
            ):
                raise RuntimeError("The LLM returned invalid RAG search criteria.")
        return {
            "search_fields": fields,
            "terms": [term.strip() for term in terms],
            "match": match,
            "constraints": constraints,
            "preferences": [preference.strip() for preference in preferences],
        }

    @classmethod
    def _criteria_values(cls, chunk: dict[str, Any], field: str) -> set[str]:
        current: list[Any] = [chunk]
        for part in field.split("."):
            next_values: list[Any] = []
            for value in current:
                if isinstance(value, list):
                    next_values.extend(
                        item[part] for item in value if isinstance(item, dict) and part in item
                    )
                elif isinstance(value, dict) and part in value:
                    next_values.append(value[part])
            current = next_values

        def flatten(value: Any) -> list[str]:
            if isinstance(value, dict):
                return [text for nested in value.values() for text in flatten(nested)]
            if isinstance(value, list):
                return [text for nested in value for text in flatten(nested)]
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return [str(value)]
            return []

        return _stems(" ".join(text for value in current for text in flatten(value)))

    @classmethod
    def _retrieve_by_criteria(
        cls, chunks: list[dict[str, Any]], criteria: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], bool]:
        terms = [_stems(term) for term in criteria["terms"]]
        fields = criteria["search_fields"]
        if not terms:
            return chunks, False
        searchable = {
            chunk["recipe_id"]: set().union(
                *(cls._criteria_values(chunk, field) for field in fields)
            )
            for chunk in chunks
        }

        def matches(chunk: dict[str, Any], require_all: bool) -> bool:
            tokens = searchable[chunk["recipe_id"]]
            matched = [bool(term and term <= tokens) for term in terms]
            return all(matched) if require_all else any(matched)

        require_all = criteria["match"] == "all"
        found = [chunk for chunk in chunks if matches(chunk, require_all)]
        approximate = False
        if not found and require_all:
            found = [chunk for chunk in chunks if matches(chunk, False)]
            approximate = bool(found)
        return found, approximate

    def _interpret_query_with_llm(self, query: str) -> dict[str, Any]:
        chunks = self.index.list_chunks()
        if not chunks:
            return {"chunks": [], "criteria": None}
        schema = self._available_chunk_schema(chunks)
        try:
            response = ask(
                json.dumps(
                    {
                        "user_request": query,
                        "available_chunk_schema": schema,
                    },
                    ensure_ascii=False,
                ),
                system=SEARCH_QUERY_PROMPT,
            )
        except Exception as exc:
            raise RuntimeError("The LLM could not interpret the recipe search request.") from exc
        criteria = self._parse_query_criteria(response, set(schema))
        return {"chunks": chunks, "criteria": criteria}

    @staticmethod
    def _available_chunk_schema(chunks: list[dict[str, Any]]) -> dict[str, str]:
        schema = {
            "recipe_id": SEARCH_FIELD_DESCRIPTIONS["recipe_id"],
            "source_path": SEARCH_FIELD_DESCRIPTIONS["source_path"],
            "page": SEARCH_FIELD_DESCRIPTIONS["page"],
            "content": SEARCH_FIELD_DESCRIPTIONS["content"],
        }
        metadata_fields = {
            key
            for chunk in chunks
            for key in chunk.get("metadata", {})
        }
        for key in sorted(metadata_fields):
            base_path = f"metadata.{key}"
            schema[base_path] = SEARCH_FIELD_DESCRIPTIONS.get(
                base_path, f"Recipe metadata field: {key}"
            )
            for chunk in chunks:
                value = chunk.get("metadata", {}).get(key)
                if isinstance(value, list):
                    nested_keys = {
                        nested_key
                        for item in value
                        if isinstance(item, dict)
                        for nested_key in item
                    }
                    for nested_key in nested_keys:
                        path = f"{base_path}.{nested_key}"
                        schema[path] = SEARCH_FIELD_DESCRIPTIONS.get(
                            path, f"{key} metadata subfield: {nested_key}"
                        )
        return schema

    @staticmethod
    def _selected(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
        metadata = candidate["metadata"]
        return {
            "status": "selected",
            "reason": reason,
            "recipe": {
                "name": metadata["name"],
                "difficulty": metadata["difficulty"],
                "ingredients": metadata["ingredients"],
                "allergens": metadata["allergens"],
                "servings": metadata["servings"],
                "utensils": metadata["utensils"],
                "instructions": metadata["instructions"],
                "ingredient_status": metadata["ingredient_status"],
                "allergen_status": metadata["allergen_status"],
                "source": candidate.get("source") or candidate["source_path"],
                "content": candidate["content"],
            },
        }

    def start_search(self, query: str) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("Please describe the dessert you are looking for.")
        if self.index.count() == 0:
            return {
                "status": "no_match",
                "message": "No recipes are indexed yet. Add recipe documents and refresh the recipe library.",
            }
        interpreted = self._interpret_query_with_llm(query)
        candidates, approximate = self._retrieve_by_criteria(
            interpreted["chunks"], interpreted["criteria"]
        )
        if not candidates:
            return {
                "status": "no_match",
                "message": self._no_match_message(query)
                if self._has_exclusions(query)
                else "No matching recipe was found. Try describing a different dessert.",
            }
        candidates = self._prefilter_locally(candidates, interpreted["criteria"])
        if not candidates:
            return {
                "status": "no_match",
                "message": self._no_match_message(query)
                if self._has_exclusions(query)
                else "No matching recipe was found. Try describing a different dessert.",
            }
        candidates = self._rank_candidates(candidates, interpreted["criteria"])[
            :MAX_FILTER_CANDIDATES
        ]
        candidates, recommendation = self._filter_candidates_with_llm(
            query, candidates, interpreted["criteria"]
        )
        candidates = self._eligible_candidates(query, candidates)
        if recommendation:
            recommended_candidate = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate["recipe_id"] == recommendation["recipe_id"]
                ),
                None,
            )
            if not recommended_candidate:
                # Backend safety rejected the recommendation; never present it.
                recommendation = None
        if recommendation:
            session_id = str(uuid.uuid4())
            state = {
                "chosen": [],
                "offset": 0,
                "candidate_ids": [recommended_candidate["recipe_id"]],
                "approximate": approximate,
                "terms": [],
            }
            self._save(session_id, query, state, "complete")
            reason = recommendation["reason"]
            if approximate:
                reason = (
                    "No recipe matched every retrieval term, so this is an approximate "
                    f"match. {reason}"
                )
            return self._selected(recommended_candidate, reason)
        terms = list(dict.fromkeys(
            token
            for term in interpreted["criteria"]["terms"]
            for token in _stems(term)
        ))
        state = {
            "chosen": [],
            "offset": 0,
            "candidate_ids": [candidate["recipe_id"] for candidate in candidates],
            "approximate": approximate,
            "terms": terms,
        }
        return self._advance(str(uuid.uuid4()), query, state, candidates)

    def refine_search(self, session_id: str, action: str, value: str | None = None) -> dict[str, Any]:
        session = self.index.get_search_session(session_id)
        if not session or session["status"] != "waiting" or not isinstance(session["transcript"], dict):
            raise ValueError("This recipe search has expired or is no longer awaiting a choice.")
        query = session["initial_query"]
        state = session["transcript"]
        chunks_by_id = {chunk["recipe_id"]: chunk for chunk in self.index.list_chunks()}
        candidate_ids = state.get("candidate_ids")
        if not isinstance(candidate_ids, list) or any(not isinstance(item, str) for item in candidate_ids):
            raise RuntimeError("The saved recipe search candidates are invalid.")
        candidates = [chunks_by_id[item] for item in candidate_ids if item in chunks_by_id]
        candidates = self._eligible_candidates(query, candidates)
        terms = state.get("terms", [])
        if not isinstance(terms, list):
            raise RuntimeError("The saved recipe search terms are invalid.")
        candidates = [
            item for item in candidates
            if all(name in self._ingredient_names(item) for name in state.get("chosen", []))
        ]

        if action == "recipe":
            if len(candidates) > FINAL_CHOICE_LIMIT:
                raise ValueError("Please narrow the search with the ingredient options first.")
            candidate = next((item for item in candidates if item["recipe_id"] == value), None)
            if candidate is None:
                raise ValueError("That recipe is not one of the offered options.")
            self._save(session_id, query, state, "complete")
            return self._selected(candidate, "You chose this recipe from the closest matches.")
        if action == "ingredient":
            used = set(terms)
            for name in state["chosen"]:
                used |= _stems(name)
            if value not in self._ranked_ingredients(candidates, used):
                raise ValueError("That ingredient is not one of the offered options.")
            state = {"chosen": [*state["chosen"], value], "offset": 0}
            candidates = [
                candidate
                for candidate in candidates
                if value in self._ingredient_names(candidate)
            ]
        elif action == "more":
            state = {
                **state,
                "offset": state.get("offset", 0) + OPTION_COUNT,
            }
        else:
            raise ValueError("Unknown refinement action.")
        state["candidate_ids"] = [candidate["recipe_id"] for candidate in candidates]
        state["approximate"] = bool(state.get("approximate"))
        state["terms"] = terms
        return self._advance(session_id, query, state, candidates)

    def _save(self, session_id: str, query: str, state: dict[str, Any], status: str) -> None:
        self.index.save_search_session(
            session_id=session_id,
            initial_query=query,
            transcript=state,
            clarification_count=0,
            status=status,
        )

    def _advance(
        self,
        session_id: str,
        query: str,
        state: dict[str, Any],
        candidates: list[dict[str, Any]],
    ) -> dict[str, Any]:
        approximate = bool(state.get("approximate"))
        terms = state.get("terms", [])
        state["candidate_ids"] = [candidate["recipe_id"] for candidate in candidates]
        if not candidates:
            self._save(session_id, query, state, "complete")
            return {
                "status": "no_match",
                "message": self._no_match_message(query)
                if self._has_exclusions(query)
                else "No matching recipe was found. Try describing a different dessert.",
            }
        if len(candidates) == 1:
            self._save(session_id, query, state, "complete")
            return self._selected(
                candidates[0],
                "This is the closest match in the recipe collection."
                if approximate
                else "This is the only recipe that matches your request.",
            )

        context = {
            "session_id": session_id,
            "candidate_count": len(candidates),
            "approximate": approximate,
            "chosen_ingredients": state["chosen"],
        }
        if approximate:
            context["message"] = (
                "No recipe matched every term in your request, so these results are approximate."
            )
        if len(candidates) <= FINAL_CHOICE_LIMIT:
            self._save(session_id, query, state, "waiting")
            return {
                **context,
                "status": "choose",
                "options": [
                    {"value": item["recipe_id"], "label": item["metadata"]["name"]}
                    for item in candidates
                ],
            }

        used = set(terms)
        for name in state["chosen"]:
            used |= _stems(name)
        page = self._ranked_ingredients(candidates, used)[state["offset"]:state["offset"] + OPTION_COUNT]
        if not page:
            self._save(session_id, query, state, "complete")
            return {"status": "restart", "message": RESTART_MESSAGE}
        self._save(session_id, query, state, "waiting")
        return {
            **context,
            "status": "refine",
            "options": [{"value": name, "label": name[:1].upper() + name[1:]} for name in page],
        }
