# Process diagram

Mermaid diagrams of the Pie Hard flows. They show where the RAG index is queried and where the LLM (`ask` in `src/llm.py`) is called.

Legend: `[(...)]` RAG index (SQLite) · `[[...]]` LLM call (`ask`) · plain boxes are deterministic backend logic.

## Macro process

The orange box is the external LLM. It is not part of the implemented solution and is reached only through the `ask` function (`src/llm.py`). Every orange arrow crossing the solution boundary is an `ask` call.

```mermaid
flowchart LR
    subgraph SOL[Pie Hard solution - implemented]
        direction LR
        DOC[resources/ recipe documents]
        ING[Ingestion and indexing]
        IDX[(RAG index: one chunk per recipe, with metadata and source)]
        SRCH[Search and ingredient refinement]
        CRIT[Validate LLM search criteria]
        FILTER[Validate LLM candidates and enforce safety rules]
        FRONT[Search page]
        BACK[Configure chunks page]
    end

    subgraph EXT[External service - outside the implemented solution]
        LLM{{LLM reached through ask}}
    end

    DOC -->|1. recipe unit| ING
    ING ==>|"2. ask: extract and translate to English"| LLM
    LLM ==>|3. recipe JSON| ING
    ING -->|4. write chunks| IDX
    FRONT -->|5. full natural-language request| SRCH
    SRCH ==>|"6. ask: request + available metadata schema"| LLM
    LLM ==>|"7. positive terms, exclusions, quantities, preferences"| CRIT
    CRIT -->|8. query matching chunks| IDX
    IDX -->|9. all matching candidate chunks| SRCH
    SRCH ==>|"10. ask: full request + search criteria + complete chunks + ALL metadata"| LLM
    LLM ==>|"11. matching IDs + optional recommendation and reason"| FILTER
    SRCH --> FILTER
    FILTER -->|12. no match or one recommended recipe| FRONT
    FILTER -->|13. several without soft preference: existing choice flow| SRCH
    BACK -->|14. row and instruction| UPD[Chunk update]
    IDX -->|15. read chunk| UPD
    UPD ==>|"16. ask: update metadata"| LLM
    LLM ==>|17. updated metadata| UPD
    UPD -->|18. write chunk| IDX

    classDef llm fill:#ffe0b2,stroke:#e65100,stroke-width:3px,color:#000
    classDef rag fill:#e3f2fd,stroke:#1565c0,color:#000
    class LLM llm
    class IDX rag
    style SOL fill:#f5f5f5,stroke:#616161
    style EXT fill:#fff3e0,stroke:#e65100,stroke-width:3px,stroke-dasharray: 6 4
    linkStyle 1,2,5,6,9,10,17,18 stroke:#e65100,stroke-width:3px
```

Search first sends the full request and available metadata schema to the external LLM through `ask`; it separates positive ingredient terms (including quantity-limited ingredients), exclusions, numeric constraints, and soft preferences. The application retrieves all matching RAG chunks, then sends every retrieved chunk with all metadata and the criteria to `ask` again. The LLM filters hard criteria and, when a soft preference is present, recommends one of the remaining candidates with a grounded reason. The application validates IDs and independently applies safety rules before showing the recommendation or existing refinement choices. Ingestion and backoffice edits also call the external LLM through `ask`.

## 1. Ingestion and indexing (LLM)

```mermaid
flowchart TD
    A[resources/ documents] --> B[Split into units: one PDF page per unit]
    B --> C{Unit hash unchanged and evaluator version current?}
    C -- yes --> D[Skip: keep existing chunks]
    C -- no --> E[["EXTERNAL LLM - ask #1: extract and translate recipes to English (JSON)"]]
    E --> F{Valid JSON?}
    F -- no --> M{Attempts left, max 5?}
    M -- yes --> E
    M -- no --> N[Report extraction error]
    F -- yes --> O{Text still not English?}
    O -- yes --> G[["EXTERNAL LLM - ask #2: rewrite the result in English"]]
    G --> P{Valid JSON and English?}
    P -- no --> M
    P -- yes --> H
    O -- no --> H[Derive allergen groups from recognized ingredients]
    H --> I[(RAG index: one chunk per recipe, with metadata and source)]
    I --> J[Remove stale chunks of changed units]

    classDef llm fill:#ffe0b2,stroke:#e65100,stroke-width:3px,color:#000
    class E,G llm
```

## 2. Recipe search (RAG retrieval and LLM filtering)

First, the application calls the external LLM through `ask` with the full original request and available chunk schema. It returns positive ingredient terms, exclusions, quantity constraints, and soft preferences separately. For "chocolate with less than 100 g flour, without milk, appealing to children", positive terms are chocolate and flour; milk is excluded, the flour limit is strict, and child-friendliness is a preference. The application queries the RAG with no fixed result limit. Next, it sends the full request, criteria, each complete retrieved chunk, and all metadata to `ask` again. The LLM applies hard filters (exactly 100 g flour does not satisfy "less than 100 g") and recommends one eligible recipe based on the soft preference. Without a soft preference, existing deterministic ingredient and recipe-name refinement remains available.

```mermaid
flowchart TD
    U[User types a natural-language request] --> API["POST /api/search"]
    API --> QI[Send full request and available metadata schema]
    subgraph EXT[External LLM - outside the solution]
        QLLM[["ask: interpret request into RAG criteria"]]
        LLM[["ask: filter candidates by natural-language criteria"]]
    end
    QI ==>|ask request| QLLM
    QLLM ==>|positive terms, exclusions, quantities, preferences| QC[Validate criteria]
    QC --> RQ[(RAG query using validated LLM criteria; local pre-filter, relevance ranking, best 12)]
    RQ --> C[Complete matching chunks]
    C --> S[Send full request and full chunks with all metadata]
    S ==>|ask request| LLM
    LLM ==>|matching IDs and optional recommended ID + reason| T[Validate IDs and apply backend safety rules]
    T --> PREF{Soft preference requested?}
    PREF -- yes --> Y[Return recommended recipe and grounded reason]
    PREF -- no --> W{Number of candidates}
    W -- 0 --> X[No match message]
    W -- 1 --> Y[Return the recipe]
    W -- 2 to 3 --> Z[Recipe-name buttons]
    W -- 4 or more --> AA[Up to 6 most frequent ingredient buttons]
    AA --> AB["POST /api/refine"]
    Z --> AB
    AB --> AC[(RAG query: read the session state and chunks)]
    AC --> AD{User action}
    AD -- ingredient --> AE[Keep candidates containing it]
    AD -- None of these --> AF[Show next 6 ingredients]
    AD -- recipe name --> Y
    AE --> W
    AF --> AG{Options left?}
    AG -- yes --> AA
    AG -- no --> AH[Ask the user to enter the request again with more criteria]
    Y --> AI[Visual layer displays the complete chunk and metadata]

    classDef llm fill:#ffe0b2,stroke:#e65100,stroke-width:3px,color:#000
    class QLLM,LLM llm
    style EXT fill:#fff3e0,stroke:#e65100,stroke-width:3px,stroke-dasharray: 6 4
```

## 3. Backoffice: Configure chunks (LLM)

```mermaid
flowchart TD
    A[Open /backoffice] --> B["GET /api/chunks"]
    B --> C[(RAG query: list all chunks)]
    C --> D[Sortable table]
    D --> E[User selects a row and writes an instruction]
    E --> F["POST /api/chunks/id/update"]
    F --> G[(RAG query: get the chunk)]
    G --> H[["EXTERNAL LLM - ask #3: chunk and instruction, returns updated metadata (JSON)"]]
    H --> I{Valid structure, in English?}
    I -- no --> J[Show the error, chunk unchanged]
    I -- yes --> K[(RAG update: save metadata and manual edit)]
    K --> L[Refresh the table]

    classDef llm fill:#ffe0b2,stroke:#e65100,stroke-width:3px,color:#000
    class H llm
```

## Summary of LLM calls

| # | Where | Purpose | Code |
|---|-------|---------|------|
| 1 | Ingestion | Extract and translate each recipe unit | `src/recipe_index.py` |
| 2 | Ingestion (fallback) | Rewrite untranslated output in English | `src/recipe_index.py` |
| 3 | Recipe search | Convert the full request and metadata schema into RAG retrieval criteria | Search backend via `src/llm.py` |
| 4 | Recipe search | Filter full retrieved chunks and all metadata against the full natural-language request | Search backend via `src/llm.py` |
| 5 | Backoffice | Update chunk metadata from a user instruction | `src/recipe_chunk_editor.py` |

Ingredient refinement after LLM filtering does not call the LLM.
