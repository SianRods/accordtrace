# Initial pilot findings

Date: 18 November 2026. Dataset: 18 source-verified definition questions, three per agreement across contract_0 through contract_5. No expert legal adjudication or broad accuracy claim.

Snapshot: `a2c03b5f-c2cb-5798-b65e-aaf5fc025b63`.

## Measured runs

| Observation | Auto-mode capture | Exact/source-only control |
|---|---:|---:|
| Planned / observed cases | 18 / 18 | 18 / 18 |
| Complete captures | 18 | 17 |
| Actual retrieval modes | 18 lexical fallbacks | 17 exact |
| Evidence recall@6 | 0.3333 over 18 | 1.0000 over 17 successful captures |
| Mean annotated-span coverage, full packet | 0.7778 over 18 | 0.9973 over 17 |
| Required evidence recovered over all planned cases, 95% span criterion | 14/18 = 0.7778 | 17/18 = 0.9444 |
| Returned agreement-scope violations | 0 | 0 among captured responses |
| Canonical citation checks | Not collected | 106/106 valid |
| Hosted generation calls | 0 | 0 |

The initial auto run directory is named `pilot_hybrid`, but **it is not a genuine hybrid measurement**. Every response included `ollama_embedding_unavailable` and reported lexical mode. Docker was initially stopped and was started using existing images/volumes. The installed Ollama service was also started; its runtime matches 0.22.1, but `embeddinggemma:300m` and the expected digest are not available in its current model store. No weights were downloaded and no immutable application profile was changed.

The exact control's incomplete case is `c1_definition_1`, Reverse Termination Fee. The backend returned HTTP 409 `ambiguous_term_selector`. This is retained in the denominator; it is not silently counted as a successful lookup or “fixed” by changing the original app.

## What these results mean

- Source lookup/citation plumbing worked for 17 controls; zero-generation extraction is not evidence that a generative answer is correct.
- Degraded lexical search recovered the labelled definition somewhere in the full packet in 14 cases, but only six cases within the first six excerpts.
- The exact-control 1.0000 recall is conditional on 17 successful captures and a 95% source-range threshold, not 100% end-to-end accuracy.
- A small missing portion can contain a material qualifier. Span coverage does not establish legal completeness.
- The data deliberately exposes an ambiguous lookup and unavailable dense model rather than weakening evaluation to produce better numbers.
- Reference labels are direct-definition source checks. They do not establish multi-hop diligence quality, judge accuracy, or performance on other agreements.

## Not measured

- Genuine dense+BM25 hybrid retrieval on the current machine state.
- Live Groq generative answers or multi-turn quality in this pilot.
- Live Gemini/Ragas semantic scores: no evaluation key/model/free-tier confirmation is configured.
- Live evaluation embeddings: missing local EmbeddingGemma prevents the required compatible inference.
- True generation faithfulness: exact per-call context is not exposed by the original backend HTTP API.
- Human-adjudicated legal correctness, negative-case abstention, graph/CRAG ablations, or broad generalization.

Ragas calculation/provider-isolation tests use controlled fixtures and mocked provider output. They are not live Gemini measurements.

Full local artifacts are ignored under `testbed/.runtime/pilot_hybrid/` and `testbed/.runtime/pilot_exact/`. This tracked note preserves the initial observations without publishing runtime captures or credentials. Reproduce/refresh through the CLI and keep new cohorts separate after environment/model changes.

The backend's code, dependencies, prompts and indexes were not modified. The testbed only used authenticated public HTTP endpoints and created ordinary source-only conversations/runs. Existing backend services and the local Ollama process are left running.

## Safe continuation

1. Restore/install the exact expected local EmbeddingGemma model and verify its digest/runtime; do not change the app profile to bypass checks.
2. Use a **new** capture name for a real hybrid cohort; do not overwrite these degraded observations.
3. Configure Gemini only in testbed/.env, select a currently free-tier-verified evaluation model, and run a small cached scoring cohort.
4. Keep generation_faithfulness unavailable until verified actual-context receipts exist.
5. Review and expand the gold set before making claims about the full agentic architecture.
