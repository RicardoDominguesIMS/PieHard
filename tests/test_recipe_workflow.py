import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import create_app
from recipe_allergens import allergens_from_ingredients
from recipe_assistant import RecipeAssistant
from recipe_documents import read_recipe_units
from recipe_index import RecipeIndex


def recipe(name, ingredients, content, source):
    return {
        "name": name,
        "difficulty": "Easy",
        "ingredients": [
            {
                "name": item,
                "quantity": None,
                "source_quote": item,
                "recognition_status": "recognized",
                "recognition_note": "",
            }
            for item in ingredients
        ],
        "allergens": [],
        "servings": "4",
        "utensils": [{"name": "mixing bowl", "source_quote": "mixing bowl"}],
        "instructions": "Mix the ingredients.",
        "ingredient_status": "certain",
        "allergen_status": "assessed",
        "recipe_content": content,
        "source": source,
    }


def review(*recipes):
    return {
        "status": "ready",
        "blocking_questions": [],
        "recipes": [
            {
                **item,
                "name_evidence": item["name"],
                "recipe_content_evidence": item["recipe_content"],
                "difficulty_evidence": "Difficulty: Easy",
                "servings_evidence": "Serves 4",
                "ingredients": [
                    {
                        "name": ingredient["name"],
                        "quantity": ingredient["quantity"],
                        "source_quote": ingredient["source_quote"],
                        "recognition_status": "recognized",
                        "recognition_note": "",
                    }
                    for ingredient in item["ingredients"]
                ],
                "allergens": [],
                "utensils": [
                    {"name": "mixing bowl", "source_quote": "mixing bowl"}
                ],
                "instructions_evidence": "Mix the ingredients.",
                "uncertainties": [],
            }
            for item in recipes
        ],
    }


class RecipeWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.resources = root / "resources"
        self.resources.mkdir()
        self.index = RecipeIndex(root / "recipes.sqlite3", self.resources)
        self.search_filter_patcher = patch(
            "recipe_assistant.ask", side_effect=self._mock_search_ask
        )
        self.search_filter = self.search_filter_patcher.start()
        self.addCleanup(self.search_filter_patcher.stop)

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _mock_search_ask(prompt, system=None):
        payload = json.loads(prompt)
        if "available_chunk_schema" in payload:
            terms, _, _ = RecipeAssistant._split_query(payload["user_request"])
            schema = payload["available_chunk_schema"]
            fields = [
                field for field in ("metadata.name", "metadata.ingredients.name", "content")
                if field in schema
            ]
            return json.dumps(
                {
                    "search_fields": fields,
                    "terms": terms,
                    "match": "all",
                    "constraints": [],
                    "preferences": [],
                }
            )
        return json.dumps(
            {
                "candidate_ids": [
                    candidate["recipe_id"] for candidate in payload["candidates"]
                ],
                "recommendation": None,
            }
        )

    @patch("recipe_index.ask")
    def test_indexing_creates_one_complete_recipe_chunk_and_skips_unchanged(self, evaluate):
        source = "Berry tart\nDifficulty: Easy\n200 g strawberries\nServes 4\nmixing bowl\nMix the ingredients."
        path = self.resources / "berry.md"
        path.write_text(source, encoding="utf-8")
        tart = recipe(
            "Berry tart",
            ["strawberries"],
            source,
            "berry.md",
        )
        evaluate.return_value = json.dumps(review(tart))

        first = self.index.index_resources()
        second = self.index.index_resources()

        self.assertEqual(first["indexed"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(evaluate.call_count, 1)
        results = self.index.search("strawberries")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["metadata"]["name"], "Berry tart")
        self.assertEqual(results[0]["source"], "berry.md")
        self.assertIn("Berry tart", results[0]["content"])

    @patch("recipe_index.ask")
    def test_indexing_surfaces_unsupported_document_types(self, evaluate):
        (self.resources / "recipe.png").write_bytes(b"not a recipe text format")

        summary = self.index.index_resources()

        self.assertEqual(len(summary["errors"]), 1)
        self.assertIn("Unsupported recipe document type", summary["errors"][0]["error"])
        evaluate.assert_not_called()

    @patch("recipe_index.ask")
    def test_extraction_retries_untranslated_output_and_requests_english(self, ask):
        portuguese = {"recipes": [{"name": "Bolo", "recipe_content": "Misture a farinha com o açúcar e junte os ovos para fazer uma massa"}]}
        english = {"recipes": [{"name": "Cake", "recipe_content": "Mix the flour with the sugar."}]}
        ask.side_effect = [json.dumps(portuguese), json.dumps(english)]

        recipes = RecipeIndex._evaluate_document("Bolo")

        self.assertEqual(recipes[0]["name"], "Cake")
        self.assertEqual(ask.call_count, 2)
        self.assertIn("written in English", ask.call_args_list[0].kwargs["system"])

    @patch("recipe_index.ask")
    def test_indexing_accepts_extraction_without_hook_validation(self, ask):
        source = "Source recipe text"
        (self.resources / "recipe.txt").write_text(source, encoding="utf-8")
        extracted = review(
            recipe("Unverified tart", ["strawberries"], "different text", "recipe.txt")
        )
        extracted["status"] = "blocked"
        extracted["recipes"][0]["allergens"] = [
            {"name": "unverified allergen", "source_quote": "not source evidence"}
        ]
        ask.return_value = json.dumps(extracted)

        summary = self.index.index_resources()
        results = self.index.search("Unverified tart")

        self.assertEqual(summary["indexed"], 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["metadata"]["allergen_status"], "assessed")
        self.assertEqual(results[0]["metadata"]["allergens"], [])

    def test_unrecognized_ingredient_requires_review_but_keeps_derived_allergens(self):
        metadata = RecipeIndex._metadata(
            {
                "name": "Test dessert",
                "ingredients": [
                    {"name": "wheat flour", "recognition_status": "recognized"},
                    {
                        "name": "mystery powder",
                        "recognition_status": "unknown",
                        "recognition_note": "Ingredient identity is unclear.",
                    },
                ],
            }
        )

        self.assertEqual(metadata["ingredient_status"], "needs_review")
        self.assertEqual(metadata["allergen_status"], "needs_review")
        self.assertEqual(
            metadata["allergens"],
            [{"name": "cereals containing gluten", "source_ingredients": ["wheat flour"]}],
        )

    def test_plurals_and_generic_flour_are_not_missed(self):
        names = ["amêndoas raladas", "avelãs sem pele", "gemas", "farinha", "bolacha tipo Maria", "farinha de arroz"]
        result = {
            item["name"]: item["source_ingredients"]
            for item in allergens_from_ingredients(names)
        }
        self.assertEqual(result["tree nuts"], ["amêndoas raladas", "avelãs sem pele"])
        self.assertEqual(result["eggs"], ["gemas"])
        self.assertEqual(
            result["cereals containing gluten"], ["farinha", "bolacha tipo Maria"]
        )

    def test_allergens_are_derived_from_recipe_ingredients(self):
        ingredients = [
            {"name": "sumo de limão", "quantity": "20 g"},
            {"name": "manteiga sem sal", "quantity": "35 g"},
            {"name": "farinha de trigo", "quantity": "20 g"},
            {"name": "sementes de sésamo branco", "quantity": "35 g"},
        ]

        allergens = allergens_from_ingredients(ingredients)

        self.assertEqual(
            allergens,
            [
                {"name": "milk", "source_ingredients": ["manteiga sem sal"]},
                {"name": "cereals containing gluten", "source_ingredients": ["farinha de trigo"]},
                {"name": "sesame", "source_ingredients": ["sementes de sésamo branco"]},
            ],
        )

    def test_pdf_is_exposed_as_independent_page_units(self):
        import pymupdf

        path = self.resources / "two-pages.pdf"
        with pymupdf.open() as document:
            for text in ("First recipe page", "Second recipe page"):
                page = document.new_page()
                page.insert_text((72, 72), text)
            document.save(path)

        with patch("recipe_index.ask", return_value=json.dumps(review())) as evaluate:
            summary = self.index.index_resources()

        units = read_recipe_units(path, "two-pages.pdf")

        self.assertEqual(
            units,
            [
                ("two-pages.pdf#page=1", "First recipe page"),
                ("two-pages.pdf#page=2", "Second recipe page"),
            ],
        )
        self.assertEqual(evaluate.call_count, 2)
        self.assertEqual(summary["discovered"], 2)
        self.assertEqual(summary["recipe_count"], 0)

    def test_search_session_round_trips_transcript_and_count(self):
        self.index.save_search_session(
            "search-id",
            "chocolate",
            [{"question": "Dark or milk chocolate?", "answer": None}],
            1,
            "waiting",
        )

        session = self.index.get_search_session("search-id")

        self.assertEqual(session["clarification_count"], 1)
        self.assertEqual(session["transcript"][0]["question"], "Dark or milk chocolate?")

    def test_allergy_constraints_exclude_known_allergen_categories(self):
        self.assertEqual(
            RecipeAssistant._allergy_constraints("I have a peanut allergy"),
            {"peanuts"},
        )
        self.assertEqual(
            RecipeAssistant._allergy_constraints("I need a nut-free cake"),
            {"tree nuts"},
        )
        self.assertEqual(
            RecipeAssistant._allergy_constraints("I am not allergic to peanuts"),
            set(),
        )
        needs_review = {"metadata": {"allergens": [], "allergen_status": "needs_review"}}
        self.assertEqual(
            RecipeAssistant._eligible_candidates(
                "I have a peanut allergy", [needs_review]
            ),
            [],
        )
        self.assertIn(
            "all ingredients recognized",
            RecipeAssistant._no_match_message("I have a peanut allergy"),
        )
        assessed = {"metadata": {"allergens": [], "allergen_status": "assessed"}}
        self.assertEqual(
            RecipeAssistant._eligible_candidates(
                "I have a peanut allergy", [assessed]
            ),
            [assessed],
        )

    CAKES = [
        ("Berry cake", ["flour", "berries", "sugar"]),
        ("Lemon cake", ["flour", "lemon", "sugar"]),
        ("Apple cake", ["flour", "apple", "cinnamon"]),
        ("Carrot cake", ["flour", "carrot", "walnut"]),
        ("Chocolate cake", ["flour", "cocoa", "sugar"]),
    ]

    def _index_cakes(self, extra=()):
        cakes = [*self.CAKES, *extra]
        (self.resources / "cakes.txt").write_text("cakes", encoding="utf-8")
        records = [
            recipe(name, ingredients, f"{name}: {', '.join(ingredients)}. Bake.", "cakes.txt")
            for name, ingredients in cakes
        ]
        with patch("recipe_index.ask", return_value=json.dumps(review(*records))):
            self.index.index_resources()
        return RecipeAssistant(self.index)

    def test_query_splits_into_terms_and_exclusions(self):
        terms, groups, words = RecipeAssistant._split_query(
            "something with pineapple without eggs"
        )
        self.assertEqual((terms, groups, words), (["pineapple"], {"eggs"}, set()))
        terms, groups, words = RecipeAssistant._split_query("cake without cinnamon")
        self.assertEqual((terms, groups, words), (["cake"], set(), {"cinnamon"}))
        terms, groups, words = RecipeAssistant._split_query(
            "without milk and gluten for a dinner party with 5 people"
        )
        self.assertEqual(terms, [])
        self.assertEqual(groups, {"milk", "cereals containing gluten"})
        self.assertEqual(words, set())

    def test_search_narrows_with_ingredient_buttons_then_offers_recipe_names(self):
        assistant = self._index_cakes()

        result = assistant.start_search("cake")

        self.assertEqual(result["status"], "refine")
        self.assertEqual(result["candidate_count"], 5)
        values = [option["value"] for option in result["options"]]
        self.assertEqual(len(values), 6)
        self.assertEqual(values[0], "sugar")
        self.assertNotIn("flour", values)

        result = assistant.refine_search(result["session_id"], "ingredient", "sugar")

        self.assertEqual(result["status"], "choose")
        names = [option["label"] for option in result["options"]]
        self.assertEqual(sorted(names), ["Berry cake", "Chocolate cake", "Lemon cake"])
        self.assertEqual(result["chosen_ingredients"], ["sugar"])

        chosen = next(o["value"] for o in result["options"] if o["label"] == "Lemon cake")
        selected = assistant.refine_search(result["session_id"], "recipe", chosen)
        self.assertEqual(selected["status"], "selected")
        self.assertEqual(selected["recipe"]["name"], "Lemon cake")

    def test_search_sends_full_request_and_complete_chunks_to_ask(self):
        assistant = self._index_cakes()

        result = assistant.start_search("cake for a small gathering")

        self.assertEqual(result["status"], "refine")
        query_prompt = json.loads(self.search_filter.call_args_list[0].args[0])
        filter_prompt = json.loads(self.search_filter.call_args_list[1].args[0])
        self.assertEqual(query_prompt["user_request"], "cake for a small gathering")
        self.assertEqual(
            query_prompt["available_chunk_schema"]["metadata.ingredients.quantity"],
            "Ingredient quantities",
        )
        payload = filter_prompt
        self.assertEqual(payload["user_request"], "cake for a small gathering")
        self.assertEqual(payload["retrieval_criteria"]["terms"], ["cake", "small"])
        indexed = {chunk["recipe_id"]: chunk for chunk in self.index.list_chunks()}
        self.assertEqual(
            payload["candidates"],
            [indexed[item["recipe_id"]] for item in payload["candidates"]],
        )
        self.assertEqual(len(payload["candidates"]), 5)
        self.assertIn("every metadata field", self.search_filter.call_args_list[1].kwargs["system"])
        self.assertIn(
            "0, 1, 2, or 3", self.search_filter.call_args_list[1].kwargs["system"]
        )
        self.assertTrue(
            all(
                {"name", "difficulty", "ingredients", "allergens", "servings", "utensils"}
                <= set(candidate["metadata"])
                for candidate in payload["candidates"]
            )
        )

    def test_structured_constraints_from_first_ask_are_validated_and_forwarded(self):
        assistant = self._index_cakes()

        def structured_criteria(prompt, system=None):
            payload = json.loads(prompt)
            if "available_chunk_schema" in payload:
                return json.dumps(
                    {
                        "search_fields": ["metadata.name"],
                        "terms": ["cake"],
                        "match": "all",
                        "constraints": [
                            {
                                "field": "metadata.servings",
                                "operator": "at_most",
                                "subject": "servings",
                                "value": 5,
                            },
                            {
                                "field": "metadata.allergens.name",
                                "operator": "exclude",
                                "value": "milk",
                            },
                        ],
                        "preferences": [],
                    }
                )
            self.assertEqual(
                payload["retrieval_criteria"]["constraints"][0]["subject"], "servings"
            )
            self.assertEqual(
                payload["retrieval_criteria"]["constraints"][1]["value"], "milk"
            )
            return json.dumps({
                "candidate_ids": [item["recipe_id"] for item in payload["candidates"]],
                "recommendation": None,
            })

        self.search_filter.side_effect = structured_criteria

        result = assistant.start_search("cake for at most 5 servings and without milk")

        self.assertEqual(result["candidate_count"], 5)
        self.assertEqual(self.search_filter.call_count, 2)

    def test_numeric_constraints_accept_a_number_with_its_unit(self):
        criteria = RecipeAssistant._parse_query_criteria(
            json.dumps(
                {
                    "search_fields": ["metadata.ingredients.quantity"],
                    "terms": ["flour"],
                    "match": "any",
                    "constraints": [
                        {
                            "field": "metadata.ingredients.quantity",
                            "operator": "less_than",
                            "subject": "flour",
                            "value": "100 g",
                        }
                    ],
                    "preferences": [],
                }
            ),
            {"metadata.ingredients.quantity"},
        )

        self.assertEqual(criteria["constraints"][0]["value"], "100 g")
        self.assertEqual(criteria["constraints"][0]["operator"], "less_than")

    def test_ingredient_quantity_limit_does_not_require_positive_retrieval_term(self):
        response = json.dumps(
            {
                "search_fields": ["metadata.ingredients.quantity"],
                "terms": ["chocolate", "dessert"],
                "match": "all",
                "constraints": [
                    {
                        "field": "metadata.ingredients.quantity",
                        "operator": "less_than",
                        "subject": "flour",
                        "value": "100 g",
                    }
                ],
                "preferences": ["kid-friendly"],
            }
        )

        criteria = RecipeAssistant._parse_query_criteria(
            response, {"metadata.ingredients.quantity"}
        )
        self.assertEqual(criteria["terms"], ["chocolate", "dessert"])

    def test_search_retrieves_positive_ingredients_filters_hard_limits_and_recommends(self):
        source_path = self.resources / "chocolate-recipes.txt"
        source_path.write_text("Chocolate recipes.", encoding="utf-8")
        kid_friendly = recipe(
            "Kid-friendly chocolate biscuits",
            ["chocolate", "flour"],
            "Chocolate biscuits for children.",
            "chocolate-recipes.txt",
        )
        kid_friendly["ingredients"][0]["quantity"] = "50 g"
        kid_friendly["ingredients"][1]["quantity"] = "80 g"
        exactly_limit = recipe(
            "Chocolate flour cake",
            ["chocolate", "flour"],
            "Chocolate cake.",
            "chocolate-recipes.txt",
        )
        exactly_limit["ingredients"][0]["quantity"] = "50 g"
        exactly_limit["ingredients"][1]["quantity"] = "100 g"
        contains_milk = recipe(
            "Chocolate milk pudding",
            ["chocolate", "flour", "milk"],
            "Chocolate pudding.",
            "chocolate-recipes.txt",
        )
        contains_milk["ingredients"][0]["quantity"] = "50 g"
        contains_milk["ingredients"][1]["quantity"] = "50 g"
        contains_milk["ingredients"][2]["quantity"] = "100 ml"
        with patch(
            "recipe_index.ask",
            return_value=json.dumps(review(kid_friendly, exactly_limit, contains_milk)),
        ):
            self.index.index_resources()

        def interpret_and_filter(prompt, system=None):
            payload = json.loads(prompt)
            if "available_chunk_schema" in payload:
                return json.dumps(
                    {
                        "search_fields": ["metadata.ingredients.name"],
                        "terms": ["chocolate", "flour"],
                        "match": "all",
                        "constraints": [
                            {
                                "field": "metadata.allergens.name",
                                "operator": "exclude",
                                "value": "milk",
                            },
                            {
                                "field": "metadata.ingredients.quantity",
                                "operator": "less_than",
                                "subject": "flour",
                                "value": "100 g",
                            },
                        ],
                        "preferences": ["appealing to children"],
                    }
                )

            self.assertIn("exactly 100 g", system)
            self.assertEqual(payload["retrieval_criteria"]["terms"], ["chocolate", "flour"])
            self.assertEqual(
                {
                    candidate["metadata"]["name"]
                    for candidate in payload["candidates"]
                },
                {
                    "Kid-friendly chocolate biscuits",
                    "Chocolate flour cake",
                },
            )
            chosen = next(
                candidate
                for candidate in payload["candidates"]
                if candidate["metadata"]["name"] == "Kid-friendly chocolate biscuits"
            )
            return json.dumps(
                {
                    "candidate_ids": [chosen["recipe_id"]],
                    "recommendation": {
                        "recipe_id": chosen["recipe_id"],
                        "reason": (
                            "Its recorded chocolate and flour ingredients fit the request, "
                            "and its name identifies it as child-friendly."
                        ),
                    },
                }
            )

        self.search_filter.side_effect = interpret_and_filter
        result = RecipeAssistant(self.index).start_search(
            "I’m hosting a dinner at my place with kids; they love chocolate but are "
            "allergic to milk. I only have 100 grams of flour at home. I want an "
            "appealing, kid-friendly dessert that fits these restrictions."
        )

        retrieval_criteria = json.loads(
            self.search_filter.call_args_list[1].args[0]
        )["retrieval_criteria"]
        self.assertEqual(retrieval_criteria["terms"], ["chocolate", "flour"])
        self.assertEqual(
            {
                constraint["value"]
                for constraint in retrieval_criteria["constraints"]
                if constraint["operator"] == "exclude"
            },
            {"milk"},
        )
        self.assertTrue(
            any(
                constraint["operator"] == "less_than"
                and constraint["subject"] == "flour"
                and constraint["value"] == "100 g"
                for constraint in retrieval_criteria["constraints"]
            )
        )
        self.assertEqual(
            retrieval_criteria["preferences"], ["appealing to children"]
        )
        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["recipe"]["name"], "Kid-friendly chocolate biscuits")
        self.assertIn("child-friendly", result["reason"])
        self.assertEqual(self.search_filter.call_count, 2)

    def test_quantity_limited_ingredient_is_left_for_llm_to_evaluate(self):
        source = "Banana recipes."
        (self.resources / "banana.txt").write_text(source, encoding="utf-8")
        no_eggs = recipe("Banana delight", ["banana"], "Banana delight.", "banana.txt")
        one_egg = recipe("Banana bake", ["banana", "egg"], "Banana bake.", "banana.txt")
        three_eggs = recipe("Banana tart", ["banana", "egg"], "Banana tart.", "banana.txt")
        four_eggs = recipe("Banana cake", ["banana", "egg"], "Banana cake.", "banana.txt")
        one_egg["ingredients"][1]["quantity"] = "1"
        three_eggs["ingredients"][1]["quantity"] = "3"
        four_eggs["ingredients"][1]["quantity"] = "4"
        with patch("recipe_index.ask", return_value=json.dumps(review(
            no_eggs, one_egg, three_eggs, four_eggs
        ))):
            self.index.index_resources()
        assistant = RecipeAssistant(self.index)

        result = assistant.start_search("banana at most 3 eggs without gluten")

        query_criteria = json.loads(self.search_filter.call_args_list[0].args[0])
        payload = json.loads(self.search_filter.call_args_list[1].args[0])
        self.assertEqual(
            {candidate["metadata"]["name"] for candidate in payload["candidates"]},
            {"Banana delight", "Banana bake", "Banana tart", "Banana cake"},
        )
        self.assertEqual(query_criteria["user_request"], "banana at most 3 eggs without gluten")
        self.assertEqual(result["candidate_count"], 4)
        self.assertEqual(
            RecipeAssistant._split_query("banana at most 3 eggs without gluten")[0],
            ["banana"],
        )

    def test_llm_filter_is_applied_and_candidate_set_is_retained_for_refinement(self):
        assistant = self._index_cakes()

        def keep_three(prompt, system=None):
            payload = json.loads(prompt)
            if "available_chunk_schema" in payload:
                return self._mock_search_ask(prompt, system)
            candidates = payload["candidates"]
            return json.dumps(
                {
                    "candidate_ids": [
                        candidate["recipe_id"] for candidate in candidates[:3]
                    ],
                    "recommendation": None,
                }
            )

        self.search_filter.side_effect = keep_three
        result = assistant.start_search("cake")

        self.assertEqual(result["status"], "choose")
        labels = {option["label"] for option in result["options"]}
        self.assertEqual(len(labels), 3)
        selected = assistant.refine_search(
            result["session_id"], "recipe", result["options"][0]["value"]
        )
        self.assertEqual(selected["status"], "selected")
        self.assertIn(selected["recipe"]["name"], labels)
        self.assertEqual(self.search_filter.call_count, 2)

    def test_invalid_llm_candidate_ids_fail_explicitly(self):
        assistant = self._index_cakes()
        self.search_filter.side_effect = None
        self.search_filter.return_value = json.dumps(
            {
                "search_fields": ["metadata.not_a_field"],
                "terms": ["cake"],
                "match": "all",
                "constraints": [],
                "preferences": [],
            }
        )

        with self.assertRaisesRegex(RuntimeError, "invalid RAG search criteria"):
            assistant.start_search("cake")

        self.search_filter.side_effect = self._mock_search_ask

        def invalid_filter(prompt, system=None):
            if "available_chunk_schema" in json.loads(prompt):
                return json.dumps(
                    {
                        "search_fields": ["metadata.name"],
                        "terms": ["cake"],
                        "match": "all",
                        "constraints": [],
                        "preferences": [],
                    }
                )
            return json.dumps({
                "candidate_ids": ["not-a-retrieved-chunk"],
                "recommendation": None,
            })

        self.search_filter.side_effect = invalid_filter
        with self.assertRaisesRegex(RuntimeError, "invalid recipe-filter results"):
            assistant.start_search("cake")

        self.search_filter.return_value = "not json"
        self.search_filter.side_effect = lambda prompt, system=None: (
            json.dumps(
                {
                    "search_fields": ["metadata.name"],
                    "terms": ["cake"],
                    "match": "all",
                    "constraints": [],
                    "preferences": [],
                }
            )
            if "available_chunk_schema" in json.loads(prompt)
            else "not json"
        )
        with self.assertRaisesRegex(RuntimeError, "invalid recipe-filter results"):
            assistant.start_search("cake")

    def test_none_of_these_shows_more_ingredients_then_asks_to_search_again(self):
        assistant = self._index_cakes()
        result = assistant.start_search("cake")
        first = [option["value"] for option in result["options"]]

        result = assistant.refine_search(result["session_id"], "more")
        self.assertEqual(result["status"], "refine")
        self.assertEqual(len(result["options"]), 2)
        self.assertFalse(set(first) & {option["value"] for option in result["options"]})

        result = assistant.refine_search(result["session_id"], "more")
        self.assertEqual(result["status"], "restart")
        self.assertIn("more selection criteria", result["message"])

    def test_refinement_rejects_unoffered_choices(self):
        assistant = self._index_cakes()
        session_id = assistant.start_search("cake")["session_id"]

        with self.assertRaises(ValueError):
            assistant.refine_search(session_id, "ingredient", "flour")
        with self.assertRaises(ValueError):
            assistant.refine_search(session_id, "recipe", "anything")

    def test_exclusions_are_applied_before_counting_candidates(self):
        assistant = self._index_cakes(extra=[("Custard cake", ["flour", "eggs", "milk"])])

        result = assistant.start_search("cake without eggs")

        self.assertEqual(result["candidate_count"], 5)
        request_candidates = json.loads(self.search_filter.call_args_list[1].args[0])["candidates"]
        self.assertEqual(len(request_candidates), 6)
        self.assertNotIn("milk", [option["value"] for option in result["options"]])
        result = assistant.start_search("cake without cinnamon")
        self.assertEqual(result["candidate_count"], 5)

    def test_search_falls_back_to_approximate_matches_and_reports_no_match(self):
        assistant = self._index_cakes()

        result = assistant.start_search("walnut and lemon")
        self.assertEqual(result["status"], "choose")
        self.assertTrue(result["approximate"])
        self.assertEqual(sorted(o["label"] for o in result["options"]), ["Carrot cake", "Lemon cake"])
        self.assertIn("approximate", result["message"])

        self.assertEqual(assistant.start_search("mango")["status"], "no_match")

    def _index_caramel_recipe(self):
        source = "Flan\n1 egg\n1 caramelo\nMix."
        (self.resources / "flan.md").write_text(source, encoding="utf-8")
        flan = recipe("Flan", ["egg", "caramelo"], source, "flan.md")
        extracted = review(flan)
        extracted["recipes"][0]["ingredients"][1]["recognition_status"] = "unknown"
        with patch("recipe_index.ask") as extract:
            extract.return_value = json.dumps(extracted)
            self.index.index_resources()
        return self.index.list_chunks()[0]

    @patch("recipe_chunk_editor.ask")
    def test_backoffice_updates_selected_chunk_metadata_from_llm(self, ask):
        chunk = self._index_caramel_recipe()
        self.assertEqual(chunk["metadata"]["allergen_status"], "needs_review")
        updated = json.loads(json.dumps(chunk["metadata"]))
        updated["ingredients"][1].update(
            {"name": "caramel with butter", "recognition_status": "recognized"}
        )
        updated["servings"] = 8
        ask.return_value = json.dumps({"metadata": updated, "summary": "Clarified caramel."})
        app = create_app(self.index.database_path, self.resources)
        client = app.test_client()

        response = client.post(
            f"/api/chunks/{chunk['recipe_id']}/update",
            json={"instruction": "The caramel is made with butter."},
        )

        self.assertEqual(response.status_code, 200)
        stored = self.index.get_chunk(chunk["recipe_id"])
        self.assertEqual(stored["content"], chunk["content"])
        self.assertEqual(stored["metadata"]["servings"], 8)
        self.assertEqual(stored["metadata"]["allergen_status"], "assessed")
        groups = {a["name"] for a in stored["metadata"]["allergens"]}
        self.assertIn("milk", groups)
        self.assertEqual(stored["metadata"]["manual_edits"][0]["summary"], "Clarified caramel.")
        self.assertEqual(len(self.index.search("butter")), 1)
        self.assertEqual(len(client.get("/api/chunks").json["chunks"]), 1)

    @patch("recipe_chunk_editor.ask")
    def test_backoffice_rejects_invalid_llm_updates_without_changing_chunk(self, ask):
        chunk = self._index_caramel_recipe()
        app = create_app(self.index.database_path, self.resources)
        client = app.test_client()
        url = f"/api/chunks/{chunk['recipe_id']}/update"

        ask.return_value = "not json"
        self.assertEqual(client.post(url, json={"instruction": "x"}).status_code, 400)
        bad = {**chunk["metadata"], "difficulty": "Impossible"}
        ask.return_value = json.dumps({"metadata": bad, "summary": "x"})
        self.assertEqual(client.post(url, json={"instruction": "x"}).status_code, 400)
        self.assertEqual(client.post(url, json={"instruction": "  "}).status_code, 400)
        self.assertEqual(
            client.post("/api/chunks/missing/update", json={"instruction": "x"}).status_code, 404
        )
        self.assertEqual(self.index.get_chunk(chunk["recipe_id"])["metadata"], chunk["metadata"])

    def test_web_routes_render_page_and_validate_json_input(self):
        app = create_app(
            database_path=self.index.database_path,
            resources_path=self.resources,
        )
        app.config["TESTING"] = True
        client = app.test_client()

        page = client.get("/")
        invalid_search = client.post("/api/search", json={"query": ""})

        self.assertEqual(page.status_code, 200)
        self.assertIn(b"The dessert finder that never gives up.", page.data)
        self.assertEqual(invalid_search.status_code, 400)
        self.assertEqual(client.get("/api/status").json["recipe_count"], 0)
        self.assertIn(b"Configure chunks", client.get("/backoffice").data)

    def test_servings_constraint_accepts_plain_numeric_string_and_missing_subject(self):
        for value in (5, "5", "5 people"):
            response = json.dumps(
                {
                    "search_fields": ["metadata.ingredients.name"],
                    "terms": ["fruit"],
                    "match": "all",
                    "constraints": [
                        {"field": "metadata.servings", "operator": "at_least", "value": value}
                    ],
                    "preferences": ["very sweet"],
                }
            )
            criteria = RecipeAssistant._parse_query_criteria(
                response, {"metadata.ingredients.name", "metadata.servings"}
            )
            self.assertEqual(criteria["constraints"][0]["subject"], "servings")

    def test_local_prefilter_rejects_only_verifiable_failures(self):
        def chunk(name, servings, ingredients, allergens=()):
            return {
                "recipe_id": name,
                "metadata": {
                    "name": name,
                    "servings": servings,
                    "ingredients": [{"name": item} for item in ingredients],
                    "allergens": [
                        {"name": group, "source_ingredients": sources}
                        for group, sources in allergens
                    ],
                },
            }

        candidates = [
            chunk("ok", "12 portions", ["eggs", "chocolate"]),
            chunk("small", "4", ["eggs"]),
            chunk("unknown-servings", "Unknown", ["eggs"]),
            chunk("almonds", "12", ["chopped almonds"]),
            chunk("milk", "12", ["butter"], [("milk", ["butter"])]),
        ]
        criteria = {
            "constraints": [
                {"field": "metadata.servings", "operator": "at_least", "subject": "servings", "value": 12},
                {"field": "metadata.ingredients.name", "operator": "does_not_contain", "value": "almonds"},
                {"field": "metadata.allergens.name", "operator": "exclude", "value": "milk"},
            ]
        }

        kept = RecipeAssistant._prefilter_locally(candidates, criteria)

        self.assertEqual([c["recipe_id"] for c in kept], ["ok", "unknown-servings"])

    def test_candidates_are_ranked_by_relevance_and_limited(self):
        def chunk(name, ingredients):
            return {
                "recipe_id": name,
                "metadata": {"ingredients": [{"name": item} for item in ingredients]},
            }

        criteria = {
            "terms": ["dark chocolate", "ginger"],
            "search_fields": ["metadata.ingredients.name"],
        }
        ranked = RecipeAssistant._rank_candidates(
            [
                chunk("one-term", ["dark chocolate"]),
                chunk("both-terms", ["dark chocolate", "ginger"]),
                chunk("none", ["flour"]),
            ],
            criteria,
        )

        self.assertEqual([c["recipe_id"] for c in ranked], ["both-terms", "one-term", "none"])

    def test_large_retrieval_is_filtered_in_batches(self):
        candidates = [
            {"recipe_id": f"id-{i}", "content": "x" * 15000, "metadata": {}}
            for i in range(6)
        ]
        calls = []

        def fake_ask(prompt, system=None):
            ids = [c["recipe_id"] for c in json.loads(prompt)["candidates"]]
            calls.append(ids)
            return json.dumps({"candidate_ids": ids[:1], "recommendation": None})

        with patch("recipe_assistant.ask", side_effect=fake_ask):
            kept, recommendation = RecipeAssistant._filter_candidates_with_llm(
                "eggs", candidates, {"preferences": []}
            )

        self.assertGreater(len(calls), 1)
        self.assertEqual(sum(len(c) for c in calls), 6)
        self.assertEqual(len(kept), len(calls))
        self.assertIsNone(recommendation)


if __name__ == "__main__":
    unittest.main()
