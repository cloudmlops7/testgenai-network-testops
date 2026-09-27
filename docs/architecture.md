# TestGenAI Architecture

```mermaid
flowchart TB
    U[User Story / Acceptance Criteria] --> R[Requirement Context]
    D[RFC / PDF / DOCX / TXT / MD] --> P[Document Parser]
    P --> C[Section-aware Chunking]
    C --> E[Ollama Embeddings]
    E --> V[(Chroma Vector Store)]
    P --> N[Deterministic Normative Requirement Extraction]
    N --> Q[Requirement IDs]
    R --> G[Grounded Generation]
    Q --> G
    V --> S[Semantic Retrieval]
    S --> G
    G[Qwen3 / LLM] --> SC[Scenarios]
    G --> TC[Test Cases]
    G --> TP[Test Plan]
    TC --> T[Traceability + Coverage]
    Q --> T
    S --> EV[Evidence / Source References]
    EV --> TC
    EV --> TP
    T --> AUTO[Phase 7: pytest Automation]
```

## Design boundary

**LLM responsibilities:**
- interpret requirements
- synthesize scenarios
- design test cases
- produce test-plan prose

**Deterministic application responsibilities:**
- document parsing
- normative keyword extraction
- requirement IDs
- evidence-reference filtering
- structural validation
- traceability
- coverage calculations
- null/blank output cleanup

This boundary is intentional: the LLM is used for generation while measurable QA bookkeeping remains deterministic.
