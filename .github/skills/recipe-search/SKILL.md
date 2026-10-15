---
name: recipe-search
description: 'Search the dessert-recipe RAG using a user natural-language request, narrow the close candidates with ingredient options until three or fewer remain, and return one grounded recipe chosen by the user. Use for recipe discovery and selection.'
---

# Dessert Recipe Search

Match the user's natural-language request to the indexed recipes, resolving ambiguity without inventing recipes or recipe details.

## When to Use

- A user describes a dessert they would like to find.
- A search needs to be refined using recipe metadata or clarification answers.
- Retrieved recipes need to be ranked and one suitable option returned.

## Procedure

1. Read `AGENTS.md`, `requirements.md`, and the existing retrieval interface. Query the RAG through the project backend; do not search source documents in place of the index or invent candidate recipes.
2. Use the request and relevant recipe metadata to retrieve candidates. Consider ingredients, difficulty, allergens, servings, and utensils when the user's request makes them relevant.
3. Keep each candidate's chunk and metadata bound together. Do not combine details from different recipe results.
4. If one candidate is clearly the suitable match, return it with its complete retrieved recipe content, metadata, and source reference.
5. Retrieve every close candidate with no fixed limit. A candidate contains all positive search terms; if none does, fall back to any term and flag the results as approximate. Negation words and excluded terms are not search terms.
6. With four or more candidates, return up to six ingredient options: the ingredients most frequent in the candidates' `ingredients` metadata, ignoring ingredients already in the request and ingredients present in every candidate. When the user chooses one, keep only the candidates containing it and repeat. "None of these" returns the next six; when they run out, ask the user to enter the request again with more criteria.
7. With three or fewer candidates, return their recipe names as options for the user to choose from. With one candidate, return it directly.
8. This refinement is deterministic backend logic; do not call `ask` to choose or ask questions.
9. If the index contains no suitable candidate, clearly state that no match was found and invite the user to refine the request. Do not fabricate a fallback recipe.
10. For a request that mentions an allergy or exclusion, apply it before counting candidates and use only candidates whose ingredients are all marked recognized and whose allergen assessment is marked assessed. Recipes needing review are always excluded. If none qualify, explain that an ingredient needs identification before allergy filtering can be used.
11. Preserve uncertainty in displayed data. Never describe an unknown allergen profile as allergen-free or imply product-label or cross-contamination coverage.

## Output

Return exactly one of:

- **Selected recipe:** complete recipe content and metadata from one retrieved chunk, including its source reference.
- **Options:** up to six ingredient options (four or more candidates) or the recipe names (three or fewer candidates), each bound to its candidates.
- **Re-enter request:** a message asking for more selection criteria when the ingredient options are exhausted.
- **No match:** a clear no-match message without an invented recommendation.

All user-facing text must be in English. Retrieved chunks are in English; do not translate or alter them.
