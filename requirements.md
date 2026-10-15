# Dessert Recipe Repository Application

## Purpose

Build an application that helps a user find a suitable dessert recipe by describing what they want. The application must select a recipe from a local repository of recipe documents and present the result clearly.

## Language

All application content must be written in English: user-facing text, prompts sent to the LLM, labels, messages, and recipe results.

Source recipe documents in `resources/` may be written in another language. During ingestion the LLM must translate each recipe into English, so that everything stored in a chunk (recipe content, name, ingredients, quantities and units, utensils, instructions, notes, and allergen names) is in English. The translation must be faithful: it must not add, remove, or alter ingredients, quantities, times, temperatures, or steps. The source document and page reference remain the link back to the original text, which is not copied into the chunk. Proper names of dishes may keep their original name in parentheses after the English name when no standard English equivalent exists.

## System structure

- Preserve the current project structure and keep application code inside `src/`.
- Separate the visual presentation layer from the backend logic. The visual layer is responsible for collecting the user's request and displaying results; the backend is responsible for searching the recipe repository and returning recipe data.
- Connect the two layers through a clear interface so that presentation code does not contain recipe-search or selection logic.
- Keep source recipe documents in the dedicated `resources/` folder. Keep application code inside `src/`; store any generated metadata or retrieval artifacts outside the source-document folder.

## Recipe repository

- Treat every supported recipe document placed in `resources/` as a source that must be evaluated and indexed by the application.
- Support plain text, Markdown, HTML, text-based PDF, and DOCX source documents. Evaluate PDF pages independently and retain the page number in each source reference. Explicitly report unsupported or unreadable files/pages; never silently skip them as successfully processed recipes.
- Use the existing `ask` function in `src/llm.py` to evaluate each document, translate it into English, and extract its recipe information. Do not introduce a separate LLM client for document evaluation.
- Request source-grounded extraction, but do not programmatically validate evidence quotations or block recipe records based on semantic uncertainty.
- Identify allergen groups from the recipe's explicitly listed ingredients. Store the ingredient names supporting each identified group; do not infer product-specific or cross-contamination risks.
- Mark allergen identification as assessed when every listed ingredient is recognized. If any ingredient is not recognized, mark the assessment as needing review and identify that ingredient. Do not claim that an empty allergen list means allergen-free.
- Build a retrieval-augmented recipe repository (RAG) from the source documents and their extracted metadata.
- Use one complete recipe as one chunk. Never split a recipe across chunks, and never combine content from different recipes in the same chunk. This keeps each retrieved result self-contained and prevents recipe details from being mixed.
- Each recipe chunk is written entirely in English (translated from the source when necessary) and must contain the complete available information for that recipe, including its preparation instructions, the metadata listed below, and a reference to its originating document.
- If a source document contains multiple recipes, create one independent chunk per recipe, keeping each recipe's content and metadata associated only with that recipe.
- Extract and store these metadata fields for each recipe:
  - Name
  - Recipe difficulty
  - Ingredients
  - Allergens, derived from the explicitly listed ingredients
  - Servings
  - Utensils
- Prompt the LLM to keep metadata grounded in the document. If a field is absent, represent it as unknown or unavailable instead of guessing. Require each ingredient to be marked recognized or unknown; unknown ingredients require review before allergy filtering can treat the allergen assessment as assessed.
- Make document evaluation repeatable so newly added or changed documents can be evaluated and indexed. Avoid re-evaluating unchanged documents unnecessarily when their content can be identified as unchanged.
- The source documents remain authoritative; the application must not invent or present recipes or recipe details unsupported by them.

## User flow

1. The user enters a free-text description of the dessert they want, such as a flavor, ingredient, occasion, dietary preference, desired difficulty, or available utensils.
2. The backend retrieves every indexed recipe that is close to the user's description, using the extracted metadata (see "Candidate retrieval").
3. If exactly one candidate remains, the visual layer presents that recipe. If none remain, the system reports that no match was found.
4. If four or more candidates remain, the layout shows up to six clickable ingredient buttons (see "Ingredient refinement"). Choosing one narrows the candidates and the process repeats.
5. When three or fewer candidates remain, the layout shows one clickable button per candidate, labelled with the recipe name. The user chooses which recipe to display.
6. The visual layer presents the selected recipe and its details in English.

## Recipe result

The visual layout must include a natural-language input box where the user describes the dessert they want. After a recipe is selected, show its details in a clear recipe layout containing:

- Dessert name
- A dedicated box or section containing the preparation instructions
- A serving icon represented by forks, with one fork per serving; also show the numeric serving count so it is clear and accessible
- Difficulty, using the labels Easy, Medium, and Elaborate, with green, yellow, and red colors respectively
- An ingredients list, including quantities when available in the source document
- A list of allergens present in the recipe, based on the source document
- A list of utensils needed, based on the source document

The application may also include a brief explanation of why the recipe matches the user's description, provided that explanation is grounded in the recipe document.

## Visual and interaction requirements

- Keep the request input distinct from the recipe result so users can easily identify where to enter a search and where to read the selected recipe.
- Present difficulty using both its text label and its specified color; do not rely on color alone to communicate difficulty.
- Make the fork icon and allergen information understandable to users, including users relying on assistive technologies.

## Back-office: Configure chunks

- The application provides a back-office page at `/backoffice` with the title "Configure chunks". It is linked from the main page.
- The page has a natural-language input box and, below it, a table of every existing chunk showing its source page, name, difficulty, servings, ingredients (unknown ones flagged), allergens with their source ingredients, utensils, and allergen-assessment status.
- The user selects one table row and writes a change request in the input box. On submit, the backend sends the selected chunk (recipe content and current metadata) and the user's text to the existing `ask` function.
- The LLM may update any existing metadata of that same chunk, for example identifying an unknown ingredient or adding a missing allergen. Statements by the back-office user are treated as authoritative; recipe text remains untrusted.
- The backend validates the returned structure, rejects invalid output without changing the chunk, then stores the updated metadata and refreshes the chunk's search fields. The recipe content, source reference and chunk identity never change, and every applied edit is recorded in the chunk's `manual_edits`.
- If every ingredient is then recognized, the allergen assessment becomes assessed; otherwise it stays as needing review.
- Manual edits persist until the source page changes and is re-evaluated by indexing.
- The table updates in place after a change and an English success or error message is shown.

## Candidate retrieval

- The search does not limit the number of candidates. It returns every recipe close to the request.
- Terms that only express negation or filler (for example "something", "with", "without", "no", "free of") are not search terms. A term that the user excludes (for example "eggs" in "without eggs") is a constraint and is never used to find recipes.
- A recipe is a candidate when its name, ingredients or content contains **all** positive search terms. If no recipe contains all of them, the system falls back to recipes containing **any** positive term and tells the user that the results are approximate.
- Exclusions (allergy or "without ..." requests) are applied before candidates are counted. Only recipes whose ingredients are all recognized and whose allergen assessment is assessed are kept; recipes needing review are always excluded.

## Ingredient refinement

- When four or more candidates remain, the backend counts the ingredient names in the candidates' `ingredients` metadata (normalized to lower case, without quantities or plural forms). The ingredients already written by the user or chosen earlier are ignored, and so are ingredients present in every candidate, because they cannot tell the candidates apart.
- The six ingredients present in the most candidates are offered as clickable buttons. Fewer than six are shown when fewer are available.
- Choosing an ingredient keeps only the candidates that contain it, adds it to the search criteria, and repeats the step. The set of candidates can only shrink, so the process always ends.
- A "None of these" button shows the next six ingredients in the ranking.
- When the ingredients are exhausted and more than three candidates remain, the system asks the user to enter the request again with more selection criteria and the search restarts.
- The refinement is deterministic backend logic and does not call `ask`. The visual layer only renders the options returned by the backend.

## Empty or unsuitable results

- If the indexed repository contains no suitable recipe, the application must say so clearly and invite the user to try a different description.
- If recipe information required for the result is missing from its document, the application must identify the missing information rather than fabricate it.
- If metadata cannot be extracted confidently from a document, report relevant fields as unknown or unavailable rather than fabricating values.

## Acceptance criteria

- A user can describe a desired dessert in natural language.
- Every supported recipe document in `resources/` is evaluated using the existing `ask` function and represented in the searchable recipe index.
- Unsupported or unreadable files in `resources/` are reported as errors and are not represented as successfully indexed recipes.
- Recipe-document evaluation uses the existing `ask` function. Each ingredient is marked recognized or unknown; listed-ingredient allergen assessment is marked assessed only when all ingredients are recognized, without blocking indexing.
- The RAG index uses one complete recipe per chunk, with no recipe split across chunks and no chunk containing multiple recipes.
- Each recipe chunk contains the recipe content, its name, difficulty, ingredients, allergens, servings, utensils, and a reference to its source document.
- If a document contains multiple recipes, each is indexed as its own independent chunk with its own metadata.
- A user request is matched against the indexed recipe documents and metadata to select an appropriate recipe.
- The RAG returns all recipes close to the request, with exclusions applied before they are counted.
- With four or more candidates, up to six ingredient buttons narrow the candidates until three or fewer remain; then the buttons show recipe names and the user chooses the recipe to display.
- A "None of these" button shows further ingredients, and when they are exhausted the user is asked to enter the request again with more criteria.
- Application code remains inside `src/`, and visual presentation is separated from backend recipe-search and selection logic.
- The visual layer includes a natural-language input box and a distinct recipe result layout.
- The result displays the dessert name, preparation instructions, difficulty, ingredients, number of servings, known allergens, and utensils.
- The serving display uses one fork per serving and also provides the numeric serving count.
- Difficulty is displayed as Easy (green), Medium (yellow), or Elaborate (red), with the label visible in addition to the color.
- The application does not claim a recipe or recipe detail that is absent from the repository.
- The back-office page lists all chunks in a table and lets a user update one selected chunk's metadata through the LLM, persisting the result.
- All user-facing text is in English, including no-match and missing-information messages.
- Every indexed chunk (recipe content and all metadata) is in English, even when the source document is written in another language. Back-office edits are also stored in English.
