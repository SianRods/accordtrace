# AccordTrace

**Evidence-backed M&A agreement review using multiagent GraphRAG and corrective retrieval.**

AccordTrace aims to help transaction reviewers investigate merger agreements through
a chatbot. It will connect clauses, definitions, exceptions, and supporting documents
to explain closing conditions and termination rights, with traceable evidence and
explicit gaps when an answer cannot be established.

Example question: "When can the buyer terminate if regulatory approval is delayed,
which exceptions apply, and what evidence is missing?"

**Project status**

The Python environment, dependency lockfile, dataset downloader, and local MAUD
download are available. The chatbot, retrieval pipeline, agents, databases, and
deployment infrastructure described below are planned. No retrieval or agent
performance results have been established yet.

**Goals**

- Resolve multi-hop references across clauses, definitions, conditions, and exceptions.
- Correct incomplete retrieval before producing a finding.
- Link material claims to exact passages in a specific document version.
- Surface counterevidence, missing schedules, and unresolved interpretation.
- Measure whether graph traversal and multiple agents improve on simpler baselines.

Initial scope is public merger agreement review, starting with closing conditions
and termination rights. Complete private transaction diligence and autonomous legal
sign-off are outside this initial scope.

**Tech stack**

| Layer | Technology and purpose |
|---|---|
| Environment, available | Python 3.12, uv, `pyproject.toml`, and `uv.lock` |
| Dataset access, available | Hugging Face Hub and a validated MAUD downloader |
| Backend, planned | FastAPI and Pydantic for APIs and typed findings |
| Agent orchestration, planned | LangGraph for bounded investigations, handoffs, persistent state, and review |
| Chat interface, planned | React + TypeScript with streaming answers and an evidence panel |
| Document processing, planned | Structural text parsing; Docling for supplementary HTML/PDF documents |
| Graph database, planned | Neo4j for clause hierarchy, definitions, references, and document relationships |
| Retrieval database, planned | Qdrant for dense and BM25/sparse retrieval, with reranking |
| Application database, planned | PostgreSQL for metadata, conversations, checkpoints, findings, and evaluation runs |
| Models, planned | Configurable LLM, embedding, and reranking providers |
| Artifact storage, planned | Local storage for development; S3 for production source files and exports |
| Delivery, planned | Docker Compose locally; GitHub Actions and proposed AWS ECR/ECS deployment with managed databases |
| Testing and observability, planned | pytest, Playwright, Ragas, k6, and OpenTelemetry; optional Jaeger/Prometheus/Grafana |

Only dependencies required by implemented code are installed. Add the remaining
stack incrementally through uv.

**Dataset**

Use the [Merger Agreement Understanding Dataset (MAUD)](https://huggingface.co/datasets/theatticusproject/maud),
published by The Atticus Project with expert-supervised annotations under CC BY 4.0.
The [paper](https://aclanthology.org/2023.emnlp-main.1019/) describes 152 agreements;
the downloaded Hugging Face snapshot contains **100 contract text files** and these
annotation rows:

| Split | Rows |
|---|---:|
| Train | 25,827 |
| Development | 6,753 |
| Test | 6,651 |
| Total | 39,231 |

The approximately 158 MB download is stored in `data/maud/`, pinned to revision
`37d5c3b95d18dcd8404cc5ce3fd5069be062392f`. Its generated
`download_manifest.json` records the revision, timestamp, contract count, and CSV hashes.

Index source agreements, keeping gold answers and annotated evidence outside the
retrieval index. Preserve original/counterfactual provenance, split by agreement,
and check document coverage before evaluating a question. Plain-text agreements
support section/span citations; page highlighting requires aligned original documents.
Supplementary SEC filings and amendments are a later extension.

**Techniques and planned workflow**

1. **Structural ingestion:** Preserve section hierarchy and stable evidence spans,
   document hashes, and versions instead of relying only on fixed-size chunks.
2. **GraphRAG:** Connect explicit references and scoped definitions; record provenance
   and validation status for extracted relationships.
3. **Hybrid retrieval:** Combine semantic and lexical candidates, rerank them, and
   expand relevant clauses through the graph.
4. **Multiagent investigation:** A planner decomposes the question, an investigator
   gathers evidence, and a separate reviewer searches for exceptions and counterevidence.
5. **CRAG-inspired correction:** Detect unresolved dependencies, retrieve missing
   context or rewrite the query, and stop at defined retry/time/token limits.
6. **Evidence verification:** Validate citation locations, assess claim support, and
   preserve unresolved issues or abstain when evidence is insufficient.

Maintain corpus snapshots and stable IDs across stores so retrieval and graph
traversal use consistent document versions. The audit trail will contain source
links, retrieved passages, graph paths, tool actions, and review outcomes.

**Evaluation and expected outcome**

Compare hybrid retrieval, graph expansion, corrective retrieval, and single-agent
versus multiagent workflows under comparable model budgets. Measure evidence recall,
finding correctness, citation support, unsupported conclusions, appropriate abstention,
p95 latency, and cost. Add reviewed cases for missing schedules, exceptions, amendments,
and conversational follow-ups; use faithfulness as one metric rather than proof of correctness.

The intended deliverable is a containerized chatbot with agreement selection,
follow-up questions, cited findings, highlighted evidence spans, dependency paths,
and explicit evidence gaps. Provide Markdown/JSON finding exports, reproducible
evaluation results, traces, and deployment instructions. Demonstrate both a supported
multi-hop answer and a justified abstention.

Expected learning covers graph modeling, retrieval correction, agent coordination,
provenance, uncertainty, benchmark design, and production reliability. The central
research outcome is evidence of when the additional graph and agent complexity helps.

**Local setup**

Install Python 3.12, then run from the project root:

```powershell
python scripts/uv.py sync
python scripts/uv.py run python data/download_maud.py
```

The launcher uses an installed `uv`, or installs it in the isolated `.tools/uv`
environment. First-time setup and dataset downloads require internet access.
No environment activation is required. If `uv` is already on PATH, standard
`uv sync`, `uv add`, and `uv run` commands work directly.

**Maintaining dependencies**

```powershell
python scripts/uv.py add <package>
python scripts/uv.py remove <package>
python scripts/uv.py sync --locked
```

Use `add`/`remove` instead of installing application dependencies directly with pip.
`uv run` automatically checks the lockfile and syncs the environment before running.
Use `uv lock --upgrade` only when intentionally upgrading locked versions.
See [uv locking and syncing](https://docs.astral.sh/uv/concepts/projects/sync/).

Commit source code, `pyproject.toml`, `uv.lock`, and `.python-version`. Generated data,
local environments, caches, credentials, build outputs, and runtime files are excluded
through `.gitignore`.


### Current backend model setup

All chat roles run on Groq: GPT-OSS 20B plans and drafts/revises; GPT-OSS 120B
reviews. Local Ollama EmbeddingGemma generates embeddings. Gemini and its SDK/key
requirements have been removed. OFFLINE_MODE must remain true (local embeddings,
not offline chat). See [frontend handoff](backend/handoffs/frontend_integration_handoff.md)
and [operations](backend/runbooks/phase_3.md). Existing index data is retained.
