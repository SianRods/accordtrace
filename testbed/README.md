# AccordTrace testbed

An **independent HTTP evaluation client**, outside the original backend application. The backend stays Groq-only; Gemini is permitted only as this testbed's evaluation LLM. All evaluation embeddings use local Ollama EmbeddingGemma.

Read [the evaluation workflow and interpretation guide](evaluation_workflow.md), [pilot dataset notes](datasets/README.md), and [initial measured findings](reports/pilot_findings.md).

## What exists

- An 18-question, source-verified **definition diagnostic**: three cases for each of six contracts, contract_0 through contract_5.
- Strict source-hash/Unicode-span validation, authenticated HTTP collection, immutable snapshot pinning, durable per-case capture and safe same-run resume.
- Deterministic evidence recall/coverage, evidence-order MRR, scope checks, canonical citation integrity and answer/abstention counters.
- Optional Ragas 0.4.x adapters: Gemini-only LLM and explicit local-only Ollama embeddings. No provider factories or automatic Google/OpenAI embedding selection.
- JSON/Markdown reports with scored denominators, actual retrieval modes, degradation warnings, capture errors and pending/unavailable judge metrics.
- Tests of real Ragas arithmetic, provider boundaries, source integrity, overlap deduplication, mocked Gemini scoring and durable judge budget/cache.

**True generation faithfulness is unavailable from the current backend API**, because it does not export exact per-call model-visible context. Citation-groundedness is separately labelled as a proxy. A fresh search is never claimed to be the context used by a chat answer.

## Setup from repository root

```powershell
python scripts/uv.py sync --project testbed --locked
python scripts/uv.py run --project testbed accordtrace-testbed validate
```

This project owns `testbed/pyproject.toml`, its resolver-generated `uv.lock`, and ignored `.venv`. The original backend/root dependency files are unchanged. The existing root uv launcher can address this project without importing the backend. If uv warns that the active root VIRTUAL_ENV differs, let it use testbed/.venv; do not use --active.

Create ignored `testbed/.env` from `.env.example`, filling the backend HTTP token. During initial setup only that existing token was copied into the local ignored file; no Groq/Gemini key was copied. The client never discovers backend/.env automatically.

```powershell
python scripts/uv.py run --project testbed accordtrace-testbed preflight
python scripts/uv.py run --project testbed accordtrace-testbed preflight --embeddings
```

The second command makes real local Ollama calls and verifies the captured backend profile. It never pulls a model. The existing server and its active index must already be available.

## Capture and report

```powershell
# Auto retrieval: inspect actual mode; auto can degrade to lexical.
python scripts/uv.py run --project testbed accordtrace-testbed collect --run auto_v1 --mode retrieval

# Exact/source-only control; no hosted generation.
python scripts/uv.py run --project testbed accordtrace-testbed collect --run exact_v1 --mode extractive --search-mode exact

# Actual backend Groq chat: explicitly opt in; start with a small cohort.
python scripts/uv.py run --project testbed accordtrace-testbed collect --run chat_pilot --mode chat --limit 3 --allow-live-chat

# Record pending judge metrics and regenerate reports without provider calls.
python scripts/uv.py run --project testbed accordtrace-testbed score --run auto_v1
python scripts/uv.py run --project testbed accordtrace-testbed report --run auto_v1
```

Reports/captures live only under ignored `testbed/.runtime/<run>/`. Resume with the identical command/run name; dataset, scope, mode and snapshot identity must match. A chat timeout does not cancel the backend run: resume the saved run ID, never submit a second question to hide it. Do not share a run directory between concurrent writers. After a crash, remove a stale writer lock only after confirming its recorded process is no longer active.

## Optional Gemini judging

```powershell
python scripts/uv.py sync --project testbed --locked --extra judge
```

Configure only in testbed/.env:
```dotenv
TESTBED_GEMINI_API_KEY=<evaluation key>
TESTBED_GEMINI_MODEL=<explicit currently free-tier-verified model>
TESTBED_GEMINI_FREE_TIER_CONFIRMED=true
TESTBED_OLLAMA_URL=http://127.0.0.1:11434
TESTBED_OLLAMA_MODEL=embeddinggemma:300m
```

No default hosted model is chosen; model availability/free-tier eligibility must be checked for your account. No live Gemini evaluation was performed during initial setup because the judge credentials/model are not configured.

```powershell
# Start with a low-call retrieval metric; scores use captured outputs.
python scripts/uv.py run --project testbed --extra judge accordtrace-testbed score --run auto_v1 --judge --metrics context_recall

# On a captured substantive chat cohort:
python scripts/uv.py run --project testbed --extra judge accordtrace-testbed score --run chat_pilot --judge --metrics factual_correctness,response_relevancy,citation_groundedness_proxy
```

Supported names: context_precision, context_recall, response_relevancy, factual_correctness, citation_groundedness_proxy. Response metrics require an actual answer. They are not meaningful on a retrieval-only capture. The journal reserves physical Gemini attempts before sending, persists usage/cache, defaults to 2 requests/minute and a 40-call budget, and never silently switches providers. Some metrics need multiple calls per case.

The optional Ragas dependency graph includes OpenAI-related libraries transitively. Their presence is not provider usage: this code never constructs an OpenAI client, uses a provider factory, or sends embeddings to a cloud endpoint.

## Tests

Every `collect`, `score` and `report` command automatically generates an offline
`report.pdf` alongside JSON/Markdown under `.runtime/<run>/`. New metric names and
statistics are discovered from saved results without changing the PDF renderer.
See [PDF configuration, template and interpretation](pdf_reporting.md).
Check configuration safely with `accordtrace-testbed config`; it never prints keys.

```powershell
python scripts/uv.py run --project testbed --extra judge pytest -q -c testbed/pyproject.toml testbed/tests --basetemp=testbed/.runtime/pytest
```

These tests use mocked Gemini/local vectors where stated. They verify mechanics and calculations, not live model accuracy. The source-alignment test requires the local MAUD files; other fixtures are synthetic. The pinned Ragas legacy metrics API emits deprecation warnings; a future Ragas upgrade must preserve adapters/calculation checks explicitly.

## Tracked project files

```text
testbed/
  pyproject.toml
  uv.lock
  .gitignore
  .env.example
  README.md
  evaluation_workflow.md
  pdf_reporting.md
  HANDOFF.md
  datasets/
    README.md
    pilot.jsonl
  reports/
    pilot_findings.md
    pdf_template.json
  scripts/
    prepare_pilot.py
  src/accordtrace_testbed/
    __init__.py
    config.py
    dataset.py
    client.py
    metrics.py
    runner.py
    judges.py
    report.py
    pdf_report.py
    configuration.py
    cli.py
  tests/
    test_metrics.py
    test_boundaries.py
    test_judge.py
    test_pdf_report.py
```

No testbed code imports backend modules, accesses its databases, changes prompts/budgets, or invokes administrative ingestion/index/activation routes. Ordinary chat collection creates normal conversations/runs through the existing public API. All datasets/references remain outside the backend retrieval index.
