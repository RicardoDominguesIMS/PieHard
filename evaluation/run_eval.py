"""Runs evaluation/golden_v2.csv through the real search and fills the
"Returned recipe" and "Notes" columns with the result.

A row whose Notes start with "Expected: no match." must return no recipe.
Every other row must return a recipe that the judge (judge_prompt.md) accepts
against the request, one criterion per call.

Usage: PYTHONPATH=src python evaluation/run_eval.py [test case id ...]
"""
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from app import DEFAULT_DATABASE, DEFAULT_RESOURCES  # noqa: E402
from llm import ask  # noqa: E402
from recipe_assistant import RecipeAssistant  # noqa: E402
from recipe_index import RecipeIndex  # noqa: E402

GOLDEN = ROOT / "evaluation" / "golden_v2.csv"
JUDGE_PROMPT = (ROOT / "evaluation" / "judge_prompt.md").read_text(encoding="utf-8")
COLUMNS = ["Test Case ID", "User Input", "Expected Category", "Returned recipe", "Notes"]
RESULT_MARK = " | Result: "
NO_MATCH_MARK = "Expected: no match."

CRITERIA = {
    "ingredients": "The recipe contains every ingredient the request asks for, allowing equivalent names. The request may also ask for a number of people or an exclusion; ignore those and judge only the requested ingredients.",
    "servings": "The recipe's servings are at least the number of people in the request. If the request states no number of people, return PASS.",
    "Vegan": "The recipe contains no animal-derived ingredient (no meat; fish; egg; milk or dairy; honey; gelatin).",
    "Allergen-free": "The recipe contains none of the allergens or foods the request says must be avoided (allergy, intolerance or diet), judged from the ingredient list and the allergen list, and its allergen assessment is 'assessed'.",
}


def drive_to_result(assistant: RecipeAssistant, response: dict) -> dict:
    # The refinement steps are deterministic; always take the first offered option.
    while response.get("status") in {"refine", "choose"}:
        action = "ingredient" if response["status"] == "refine" else "recipe"
        response = assistant.refine_search(
            response["session_id"], action, response["options"][0]["value"]
        )
    return response


PEOPLE_PATTERNS = [
    r"\bfor (\d+)\b",
    r"\b(\d+) (?:people|persons|guests|portions|servings)\b",
    r"\bparty of (\d+)\b",
    r"\bthere are (\d+)\b",
]


def check_servings(request: str, recipe: dict) -> dict:
    """Deterministic check: the recipe must serve at least the requested number of people."""
    people = next(
        (int(m.group(1)) for p in PEOPLE_PATTERNS if (m := re.search(p, request, re.IGNORECASE))),
        None,
    )
    if people is None:
        return {"verdict": "PASS", "evidence": "no number of people requested"}
    numbers = re.findall(r"\d+", str(recipe.get("servings", "")))
    if not numbers:
        return {"verdict": "UNKNOWN", "evidence": f"servings: {recipe.get('servings')}"}
    served = int(numbers[0])
    verdict = "PASS" if served >= people else "FAIL"
    return {"verdict": verdict, "evidence": f"servings {served} for {people} people"}


def judge(criterion: str, request: str, recipe: dict) -> dict:
    prompt = json.dumps(
        {"request": request, "criterion": criterion, "chunk": recipe}, ensure_ascii=False
    )
    raw = ask(prompt, system=JUDGE_PROMPT)
    try:
        result = json.loads(raw)
        if result.get("verdict") in {"PASS", "FAIL", "UNKNOWN"}:
            return result
    except (ValueError, TypeError, AttributeError):
        # The judge sometimes writes unescaped quotes in the evidence; the verdict is still readable.
        match = re.search(r'"verdict"\s*:\s*"(PASS|FAIL|UNKNOWN)"', raw)
        if match:
            return {"verdict": match.group(1), "evidence": raw}
    return {"verdict": "UNKNOWN", "evidence": "invalid judge output"}


def main() -> None:
    assistant = RecipeAssistant(RecipeIndex(DEFAULT_DATABASE, DEFAULT_RESOURCES))
    with GOLDEN.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    only = set(sys.argv[1:])
    for row in rows:
        if only and row["Test Case ID"] not in only:
            continue
        request = row["User Input"]
        base_note = row["Notes"].split(RESULT_MARK)[0]
        expects_no_match = base_note.startswith(NO_MATCH_MARK)
        try:
            response = drive_to_result(assistant, assistant.start_search(request))
        except (ValueError, RuntimeError) as error:
            response = {"status": "error", "message": str(error)}
        recipe = response.get("recipe")
        row["Returned recipe"] = recipe["name"] if recipe else "NO MATCH"
        if response.get("status") == "error":
            result = f"FAIL (error: {response['message']})"
        elif expects_no_match:
            result = "FAIL (a recipe was returned)" if recipe else "PASS"
        elif not recipe:
            result = "FAIL (no recipe was returned)"
        else:
            criteria = ["ingredients", "servings"]
            if row["Expected Category"] in CRITERIA:
                criteria.append(row["Expected Category"])
            failed = []
            for name in criteria:
                verdict = (
                    check_servings(request, recipe)
                    if name == "servings"
                    else judge(CRITERIA[name], request, recipe)
                )
                if verdict["verdict"] != "PASS":
                    failed.append(f"{name}={verdict['verdict']}")
            result = "PASS" if not failed else "FAIL (" + "; ".join(failed) + ")"
        row["Notes"] = base_note + RESULT_MARK + result
        print(row["Test Case ID"], row["Returned recipe"], "->", result, flush=True)
    # The BOM keeps Excel from misreading accented characters.
    with GOLDEN.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    main()
