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

    def tearDown(self):
        self.temp.cleanup()

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

    @patch("recipe_assistant.ask")
    def test_search_asks_clarification_and_then_returns_single_recipe(self, ask):
        source = "Dessert: Berry tart strawberries Dessert: Lemon cake lemons"
        (self.resources / "recipes.txt").write_text(source, encoding="utf-8")
        one = recipe(
            "Berry tart", ["strawberries"], "Dessert: Berry tart strawberries", "recipes.txt"
        )
        two = recipe(
            "Lemon cake", ["lemons"], "Dessert: Lemon cake lemons", "recipes.txt"
        )
        with patch("recipe_index.ask", return_value=json.dumps(review(one, two))):
            self.index.index_resources()
        berry_id = next(
            item["recipe_id"] for item in self.index.search("strawberries")
            if item["metadata"]["name"] == "Berry tart"
        )
        ask.side_effect = [
            json.dumps({"action": "clarify", "question": "Would you prefer berries or citrus?"}),
            json.dumps({"action": "clarify", "question": "Would you prefer a tart or cake?"}),
        ]
        assistant = RecipeAssistant(self.index)

        result = assistant.start_search("dessert")

        self.assertEqual(result["status"], "clarification")
        ask.assert_called_once()
        session_id = result["session_id"]
        answer = assistant.answer_clarification(session_id, "berries")
        self.assertEqual(answer["status"], "clarification")
        self.assertEqual(answer["clarifications_asked"], 2)
        self.assertEqual(ask.call_count, 2)
        answer = assistant.answer_clarification(session_id, "a tart")
        self.assertEqual(answer["status"], "selected")
        self.assertEqual(answer["recipe"]["name"], "Berry tart")
        self.assertEqual(ask.call_count, 2)

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
        self.assertIn(b"What sounds", page.data)
        self.assertEqual(invalid_search.status_code, 400)
        self.assertEqual(client.get("/api/status").json["recipe_count"], 0)
        self.assertIn(b"Configure chunks", client.get("/backoffice").data)


if __name__ == "__main__":
    unittest.main()
