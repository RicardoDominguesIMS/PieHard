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
- Use the existing `ask` function in `src/llm.py` to evaluate each document, translate it into English, and extract its recipe information. Do not introduce a separate LLM client for document evaluation. `ask` uses `temperature=0` by default for repeatable results.
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
2. The backend sends the complete user request and the available chunk metadata schema to the existing `ask` function. The metadata schema lists every field stored with a chunk, including name, difficulty, ingredients and quantities, allergens, servings, utensils, recognition/review status, source, and any other stored metadata.
3. The first LLM call interprets the whole request and returns structured RAG retrieval criteria: relevant metadata fields and the search values/terms to look for in those fields. It must distinguish positive criteria from exclusions and preserve numeric constraints as written. It must not create or alter recipe records.
4. The backend queries the RAG using those LLM-generated criteria and retrieves all matching chunks, pre-filters them locally on verifiable metadata, and ranks them by relevance.
5. The backend sends the complete original request and the best-ranked pre-filtered candidate chunks (at most 12) in full to `ask` again. Include each complete recipe content, every metadata field, stable chunk ID, and source reference. The second LLM call filters out chunks that fail the complete natural-language request and returns only candidate IDs from the supplied set.
6. The backend validates both LLM responses and returned candidate identities, applies allergy/exclusion safety constraints independently, and rejects malformed output explicitly. The LLM must not create, alter, or add recipe chunks or details.
7. If no candidates remain, the system reports that no match was found. If exactly one remains, the visual layer presents that recipe directly.
8. If multiple candidates remain, the existing deterministic ingredient-refinement flow offers up to six ingredient options when there are four or more candidates. When three or fewer remain, it offers recipe-name buttons. The user narrows or chooses until one recipe is selected.
9. The visual layer presents the selected recipe and its details in English.

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

- **LLM query interpretation (first call):** send the full original user request and the exact list/schema of metadata fields available on recipe chunks to `ask`. The LLM returns structured search criteria identifying which fields to search, positive ingredient terms, exclusions, numeric constraints, and soft preferences. Only ingredients the recipe must contain are positive retrieval terms. A quantity limit that merely states availability or a cap ("I only have 100 g of flour", "I have 2 eggs", "at most 3 eggs") is not a positive term: recipes without that ingredient remain candidates (an absent ingredient counts as zero). The ingredient becomes a positive term only when the user requires it ("must include 2 eggs"). For example, "chocolate dessert, I only have 100 g of flour, no milk, appealing to children" produces the positive term `chocolate`, an exclusion for milk, an inclusive flour `at_most` constraint, and a child-friendly preference. Do not use excluded items, generic terms such as "dessert", or subjective preferences as positive terms. It must not guess metadata fields that are not present in the schema.
- **RAG retrieval:** use the first LLM response to search the corresponding chunk metadata and content. Retrieve every matching candidate, then (a) deterministically reject candidates that fail restrictions verifiable from structured metadata (numeric servings, excluded ingredient names, excluded allergen groups; unverifiable values are kept for the LLM), and (b) rank the remainder by relevance to the positive terms (terms matched, with a bonus when one ingredient name matches a whole term) and keep only the best 12 for the second LLM call to bound latency and token use. Keep the original request for the next step.
- **LLM filtering and recommendation (second call):** send the full original request, the structured retrieval criteria, and all retrieved chunks, including complete recipe content, every available metadata field, stable IDs, and source references, to `ask`. Ask it to filter candidates against all hard natural-language criteria, returning only IDs of matching chunks. Criteria may include ingredient presence, quantities, allergens, difficulty, servings, or utensils. The LLM may choose only from the supplied candidates and must not invent candidates or modify their content or metadata. If soft preferences are present (for example, appealing to children), it must also choose one recommendation from the surviving IDs and provide a concise reason grounded in that chunk.
- The two LLM steps have distinct contracts: the first determines what/how to query in the RAG; the second evaluates actual retrieved recipe records and filters them.
- For a request such as "a recipe with banana and at most 3 eggs, without gluten", the first call identifies ingredients/allergens as relevant metadata and supplies banana, eggs (maximum 3), and gluten (excluded) as retrieval criteria. After retrieval, the second call checks complete ingredient quantities and allergen metadata, keeping banana recipes with 0, 1, 2, or 3 eggs (an egg absent from the complete ingredient list counts as 0), and removing recipes whose allergen metadata lists gluten. It must not infer egg quantities when they are unavailable.
- For "chocolate dessert, I only have 100 g of flour, without milk, appealing to children", the first call returns the positive term chocolate, an inclusive flour `at_most` constraint, a milk exclusion, and the child-friendly preference. RAG retrieval gathers chocolate chunks without using flour or milk as positive terms. The second call rejects recipes with more than 100 g flour (recipes with no flour qualify), recipes listing milk, and recipes whose flour quantity cannot be reliably compared to the requested mass. It then recommends one surviving recipe based on the soft preference, with a reason grounded in the selected chunk. Compatible mass units may be converted; volume-to-mass conversions must not be guessed.
- For a request such as "suggest a recipe without milk and gluten for a dinner party with 5 people", the first call identifies allergens and servings as relevant metadata and provides milk/gluten exclusions and a five-person serving criterion. After retrieval, the second call checks the complete candidate metadata against the complete request and returns only matching chunk IDs. It must not invent serving adjustments or recipe changes.
- The backend validates that returned candidate IDs are a subset of the retrieved IDs, preserves each chunk with its own metadata and source, and reports malformed or unusable LLM output explicitly.
- Enforce explicit allergy/exclusion safety constraints in backend logic as well: when the request excludes an allergen or ingredient for allergy/safety reasons, retain only chunks whose ingredients are all recognized and whose allergen assessment is assessed, and exclude chunks needing review. Do not rely solely on the LLM for this safety gate.
- The first LLM call receives the available metadata schema; the second receives every available metadata value in each complete candidate chunk, not only fields used during retrieval. This lets the filtering call evaluate criteria such as ingredient quantities, servings, difficulty, and utensils against the full record.
- A specific unwanted food ("no almonds") is an ingredient exclusion evaluated against the complete ingredient list; allergen-group exclusions apply to allergy requests. "I have bananas" (no quantity limit) requires bananas.
- A request "for N people" means the recipe must serve at least N (`at_least`, never `equals`); a recipe with more servings qualifies.
- Large retrievals are hard-filtered by the second call in batches of complete chunks to stay within the LLM request size limit; the recommendation, when a preference exists, is chosen among the survivors. If no recommendation is returned, the hard-filtered candidates continue through deterministic refinement.
- Strict quantity operators such as `less_than` preserve the distinction between "less than 100 g" and "at most 100 g". Availability wording such as "I only have 100 g of flour" is inclusive (`at_most`), so a recipe with exactly 100 g qualifies. Unit-bearing quantities are accepted in structured criteria. Numeric comparisons that cannot be verified from the chunk must fail closed.
- The backend rejects query criteria that include an ingredient quantity constraint without also including that ingredient as a positive RAG term; this prevents a quantity-limited ingredient from being silently lost in phase one.
- Recommendation IDs must belong to the LLM-filtered candidate set and remain eligible after backend safety checks. The recommendation rationale is shown as the match reason. If no preference was provided, preserve the established deterministic option/refinement flow.
- Approximate-match status from RAG retrieval must remain visible if the retrieval step falls back to broader matches.

## Ingredient refinement

- Ingredient refinement starts only after the LLM has returned its possible-candidate set and backend safety validation has been applied. The backend then counts ingredient names in those candidates' `ingredients` metadata (normalized to lower case, without quantities or plural forms). The ingredients already written by the user or chosen earlier are ignored, and so are ingredients present in every candidate, because they cannot tell the candidates apart.
- The six ingredients present in the most candidates are offered as clickable buttons. Fewer than six are shown when fewer are available.
- Choosing an ingredient keeps only the candidates that contain it, adds it to the search criteria, and repeats the step. The set of candidates can only shrink, so the process always ends.
- A "None of these" button shows the next six ingredients in the ranking.
- When the ingredients are exhausted and more than three candidates remain, the system asks the user to enter the request again with more selection criteria and the search restarts.
- Ingredient refinement is deterministic backend logic and does not call `ask` again. The visual layer only renders the options returned by the backend.
- Each new search calls `ask` twice before candidate counting/refinement: first to generate RAG retrieval criteria from the request and available metadata schema, and second to filter retrieved chunks against the full request. Ingredient refinement does not call `ask` again.

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
- The RAG retrieves all close chunks from the words it can identify in the request; only the 12 most relevant that survive local pre-filtering go to the second LLM.
- Each search calls `ask` first with the full request and available chunk metadata schema to produce structured RAG search criteria, then with the full request and complete retrieved chunks to filter candidates.
- Both `ask` responses are validated explicitly; the first must reference only available metadata fields, and the second must return only IDs from the RAG result.
- Backend validation preserves chunk identity and enforces allergy/exclusion safety independently of the LLM.
- A new search invokes `ask` twice before candidate counting: once before RAG retrieval to separate positive terms, exclusions, numeric constraints, and soft preferences using the available chunk metadata schema, and once after retrieval to filter complete chunks and, when requested, recommend one safe candidate. Ingredient-button refinement does not invoke `ask` again.
- Zero LLM-filtered candidates produce a no-match response; one is returned directly. Multiple candidates enter the existing ingredient and recipe-name choice flow unless the request contains a soft preference, in which case the final LLM recommends one hard-filtered, backend-safe candidate directly.
- When a request includes a subjective preference, the final LLM recommends one candidate only after all hard ingredient, quantity, and allergen restrictions have been applied; the UI displays that recipe directly with the grounded rationale.
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
