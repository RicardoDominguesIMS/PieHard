# Pie Hard

Pie Hard finds a dessert recipe from source documents in `resources/`. Recipe-document evaluation uses the existing LLM `ask` function, extracted recipe data is indexed as one complete recipe per SQLite FTS chunk, and a natural-language search returns every close recipe and narrows them with clickable ingredient buttons until you pick one.

## Setup

1. Use Python 3.10 or newer and install the dependencies:

   ```bash
   python -m pip install -r requirements.txt
   ```

2. Create a local `.env` file in the project root containing your `LLM_API_KEY`:

   ```dotenv
   LLM_API_KEY=your-group-api-key
   ```

   `LLM_BASE_URL` and `LLM_MODEL` already have defaults. Keep the key in the ignored `.env` file; never put it in source code or commit it.
3. Add text-based recipe documents to `resources/`. Supported formats are Markdown, plain text, HTML, PDF (with extractable text), and DOCX. Image-only PDFs and other formats are reported as unsupported or unreadable and are not indexed.
4. Build or refresh the local recipe index:

   ```bash
   python src/app.py index
   ```

   Each changed document is submitted independently to `ask`. Unchanged documents are not submitted again. Every extracted ingredient is marked recognized or needs review; allergen groups are derived from the recognized ingredient names. Errors reading a document or parsing the LLM response are reported.

5. Start the application:

   ```bash
   python src/app.py
   ```

   Or run it in the background with `./start.sh` and stop it with `./stop.sh`. The scripts keep the process ID and log in `.run/` and honour the `HOST` and `PORT` environment variables.

6. Open <http://127.0.0.1:5000/>. Use **Refresh recipe library** to process newly added or changed documents from the interface.

The index and search sessions are stored in `data/recipes.sqlite3`; this generated file is excluded from Git. No recipe source content is sent to an external system except as the document text included in the configured LLM request through `ask`.

## Architecture

- `src/app.py` provides the Flask web interface and JSON API.
- `src/recipe_documents.py` reads supported recipe-document formats and exposes PDF pages as independently traceable ingestion units.
- `src/recipe_index.py` sends each source independently to `ask` for recipe extraction and faithfully translates each recipe into English during extraction (an untranslated result is retried, then reported as an error) and stores complete, isolated English chunks and metadata in SQLite FTS5.
- `src/recipe_allergens.py` derives listed allergen groups from named recipe ingredients and retains the ingredients supporting each group. Ingredient recognition must be complete before an allergen assessment is marked assessed.
- `src/recipe_assistant.py` retrieves every close candidate deterministically (no LLM call) and narrows them with up to six ingredient buttons per step until three or fewer remain, when the buttons show recipe names.
- `src/recipe_chunk_editor.py` powers the "Configure chunks" back-office page (`/backoffice`): it sends one selected chunk and the user's instruction to `ask`, validates the returned metadata and saves it. Edits are kept until the source page changes and is re-indexed.
- `src/templates/` and `src/static/` contain the visual layer, separate from backend recipe logic.

The search index uses SQLite FTS5 retrieval with weighted matching over recipe names and metadata. It does not use a hosted vector database or claim semantic embedding search. Allergen groups are derived only from recognized, listed ingredients. An unrecognized ingredient marks the allergen assessment as needing review; product-specific details and cross-contamination are not covered.

## Tests

Run the focused application tests with:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```
