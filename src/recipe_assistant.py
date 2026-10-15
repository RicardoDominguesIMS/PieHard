"""Deterministic candidate retrieval and ingredient-based refinement for recipe searches."""

import re
import uuid
from collections import Counter
from typing import Any

from recipe_allergens import ALLERGEN_KEYWORDS, ALLERGEN_LABELS
from recipe_index import RecipeIndex


OPTION_COUNT = 6
FINAL_CHOICE_LIMIT = 3
STOPWORDS = frozenset(
    "a an the and or of with without no not free some something anything any "
    "i me my we want wants like would love need looking look for to in on at "
    "that this is it be please give show find make dessert desserts recipe "
    "recipes something".split()
)
GENERIC_EXCLUSION_RE = re.compile(
    r"\b(?:without|excluding|exclude|avoid(?:ing)?|no)\s+(?:any\s+)?([^\W\d_]+)",
    re.IGNORECASE,
)
RESTART_MESSAGE = (
    "None of the offered ingredients narrowed the results enough. Please enter your "
    "request again with more selection criteria."
)


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
        if cls._allergy_constraints(query):
            return (
                "No suitable recipe was found with all ingredients recognized and no "
                "listed allergen matching your request."
            )
        return "No recipe matching your request was found in the available records."

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
            r"(?:(?:all|any)\s+)?(" + term_pattern + r")\b",
            re.IGNORECASE,
        )
        for match in pattern.finditer(query):
            preceding = query[max(0, match.start() - 12):match.start()].casefold().strip()
            if preceding.endswith("not"):
                continue
            spans.append(match.span())
            add_constraint(match.group(1))

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

    def _find_candidates(
        self, query: str, chosen: list[str]
    ) -> tuple[list[dict[str, Any]], bool, list[str]]:
        """Return all close candidates, whether the match is approximate, and the terms in use."""
        cls = type(self)
        terms, _, excluded_words = cls._split_query(query)
        eligible = cls._eligible_candidates(query, self.index.list_chunks())
        eligible = [
            item for item in eligible
            if not excluded_words & _stems(
                " ".join(
                    str(i.get("name", ""))
                    for i in item["metadata"].get("ingredients", [])
                    if isinstance(i, dict)
                )
            )
        ]

        def searchable(item: dict[str, Any]) -> set[str]:
            metadata = item["metadata"]
            ingredient_text = " ".join(
                str(i.get("name", "")) for i in metadata.get("ingredients", []) if isinstance(i, dict)
            )
            return _stems(f"{metadata.get('name', '')} {ingredient_text} {item['content']}")

        approximate = False
        if terms:
            matches = [item for item in eligible if all(term in searchable(item) for term in terms)]
            if not matches:
                matches = [item for item in eligible if any(term in searchable(item) for term in terms)]
                approximate = bool(matches)
        else:
            matches = eligible
        matches = [item for item in matches if all(name in cls._ingredient_names(item) for name in chosen)]
        return matches, approximate, terms

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
        constraints = cls._allergy_constraints(query)
        return [
            candidate
            for candidate in candidates
            if not constraints
            or candidate["metadata"].get("allergen_status") == "assessed"
            and not (cls._candidate_allergens(candidate) & constraints)
        ]

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
        return self._advance(str(uuid.uuid4()), query, {"chosen": [], "offset": 0})

    def refine_search(self, session_id: str, action: str, value: str | None = None) -> dict[str, Any]:
        session = self.index.get_search_session(session_id)
        if not session or session["status"] != "waiting" or not isinstance(session["transcript"], dict):
            raise ValueError("This recipe search has expired or is no longer awaiting a choice.")
        query = session["initial_query"]
        state = session["transcript"]
        candidates, _, terms = self._find_candidates(query, state["chosen"])

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
        elif action == "more":
            state = {"chosen": state["chosen"], "offset": state["offset"] + OPTION_COUNT}
        else:
            raise ValueError("Unknown refinement action.")
        return self._advance(session_id, query, state)

    def _save(self, session_id: str, query: str, state: dict[str, Any], status: str) -> None:
        self.index.save_search_session(
            session_id=session_id,
            initial_query=query,
            transcript=state,
            clarification_count=0,
            status=status,
        )

    def _advance(self, session_id: str, query: str, state: dict[str, Any]) -> dict[str, Any]:
        candidates, approximate, terms = self._find_candidates(query, state["chosen"])
        if not candidates:
            self._save(session_id, query, state, "complete")
            return {
                "status": "no_match",
                "message": self._no_match_message(query)
                if self._allergy_constraints(query)
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
