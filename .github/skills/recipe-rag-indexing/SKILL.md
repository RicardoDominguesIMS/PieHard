---
name: recipe-rag-indexing
description: 'Build or update the dessert-recipe RAG index from extracted recipe records. Use when indexing ingested recipes, refreshing changed records, or checking that recipe chunks and metadata remain isolated.'
---

# Recipe RAG Indexing

Create and maintain a searchable index in which each extracted recipe and its metadata are stored together as one retrieval unit.

## When to Use

- Extracted recipe records are ready from the recipe document ingestion workflow.
- New or changed recipes must be added to or refreshed in the RAG index.
- The recipe index needs validation for chunk boundaries, metadata, or stale records.

## Procedure

1. Read `AGENTS.md`, `requirements.md`, and the existing backend/indexing implementation before making changes. Follow existing project patterns and dependencies.
2. Accept extracted recipe records from the ingestion workflow. Do not index malformed records or content without its source reference. Source evidence is not programmatically validated.
3. Create exactly one chunk per complete recipe. Never split one recipe across chunks and never combine multiple recipes in one chunk.
4. Index only English text; every chunk field, including recipe content, must be in English. Keep the complete available recipe content, preparation instructions, source reference, and metadata together in the same chunk.
5. Store the following metadata on every chunk: `name`, `difficulty`, `ingredients`, `allergens`, `servings`, and `utensils`.
6. Derive listed allergen groups from explicitly named ingredients, retaining the ingredient names supporting each group. Do not infer product-specific or cross-contamination risks.
7. Mark the allergen assessment as assessed only when every ingredient is recognized. If an ingredient is unknown, mark the assessment as needing review and preserve the ingredient's recognition note.
8. Preserve unknown or unavailable values explicitly. Do not convert missing allergen data into an allergen-free claim.
9. If a source document contains several recipes, index each recipe as a distinct chunk with its own recipe-specific metadata.
10. Make indexing repeatable. Refresh records when source content changes and avoid duplicate or stale chunks when a recipe is reprocessed.
11. Use the repository's existing storage or vector index if available. Do not introduce a new vector database, embedding provider, or indexing dependency without a demonstrated need and approval.
12. Validate that each indexed chunk has exactly one recipe, all required metadata keys, and a traceable source reference. Add or update focused tests for chunk isolation and idempotent updates.
13. Surface indexing failures explicitly; do not report a successful index update when a record failed structural parsing or storage.

## Handoff

Provide the user-search workflow with the index interface, the exact chunk metadata schema, and the retrieval result shape. RAG search must return all matching candidates (the search workflow later pre-filters and ranks them before the LLM call), each as a complete recipe chunk with all metadata (name, difficulty, full ingredients and quantities, allergens, servings, utensils, recognition/review status, and any other stored fields), stable chunk ID, and source reference. Do not drop metadata when preparing results for search-time filtering.

The search workflow first sends the full original request and the exact metadata schema to the existing `ask` function to get structured RAG query criteria that reference only available fields. Positive ingredient terms include every ingredient with a quantity limit; each such constraint's ingredient subject must be among the positive terms. Exclusions and soft preferences are separate, not positive terms. After RAG retrieval, it sends the full request, criteria, and complete retrieved chunks to `ask` again for natural-language filtering. The filtering LLM returns only IDs from the supplied candidate set. If a soft preference is present, it also recommends one of the hard-filtered candidates and provides a reason grounded in that chunk. It must not create or modify chunks. Keep indexing itself separate from these search-time LLM calls.
