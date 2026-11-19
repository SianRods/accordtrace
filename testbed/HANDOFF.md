# Independent evaluation handoff

## PDF reporting update - 18 November 2026

User authorized secret-safe `.env` checking and automatic extensible PDF reporting.
All new code/dependencies/docs remain inside `/testbed`; application code is unchanged.

- Effective local configuration has backend token, Gemini evaluation key,
  `gemini-2.5-flash-lite`, confirmed free-tier flag, and local Ollama settings.
  Missing source path/budgets/timeouts were appended with documented defaults.
  Values of secrets were neither printed nor copied into reports. No live Gemini
  check or new evaluation was performed. Historical missing-key reasons in stored
  scores describe those old captures, not current configuration readiness.
- Added offline `config` command, `configuration.py`, `pdf_report.py`, editable
  `reports/pdf_template.json`, `pdf_reporting.md`, and `tests/test_pdf_report.py`.
  `report.build` now generates PDF after JSON/Markdown. CLI collect/score/report
  hold a writer lock through report generation. A valid partial manifest triggers
  an attempted report on handled collection/scoring exit, including exceptions.
- PDF discovers all metric names/statistics, per-contract fields and extra report
  metadata. Optional template interpretations do not whitelist metric inclusion.
  Aggregate reports now include captured judge profiles and status/reason counts.
  Future metric calculation/aggregation still requires its own implementation/tests.
- uv regenerated `testbed/uv.lock` and synced the independent environment:
  ReportLab 4.5.1 runtime, pypdf 6.21.4 and pypdfium2 5.15.2 dev dependencies.
  Root/backend dependencies were unchanged. Normal PDF generation is offline.
- Verification: `python scripts/uv.py run --project testbed --extra judge --offline
  pytest -q --disable-warnings -c testbed/pyproject.toml testbed/tests
  --basetemp=testbed/.runtime/pytest` -> 26 passed, 18 existing Ragas deprecations.
  Tests cover automatic output, future metric/statistic discovery, nulls, long
  tables, safe text escaping, atomic PDF replacement, secret-safe config and
  partial collection failures. They do not certify live judge quality.
- Regenerated ignored `pilot_exact/report.pdf` and `pilot_hybrid/report.pdf` from
  existing captures only. Rendered every page with local PDFium and inspected both
  full overviews plus the detailed first page. Preview images remain ignored under
  `.runtime/pdf_preview/`. MiKTeX pdftoppm could not run because its global setup is
  unfinished; no global installation was modified.
- See `pdf_reporting.md` for commands, recovery and interpretation. Closing a PDF
  viewer may be necessary for replacement on Windows. Forced termination/disk or
  rendering failure can prevent finalization; `report --run <name>` safely retries
  from saved captures without provider calls. Existing model availability and
  quality-measurement limitations below remain applicable.

Updated 18 November 2026. User authorized a separate /testbed evaluation project,
metric reporting for the initial pilot, detailed workflow documentation, and root
AGENTS.md changes. This work does not modify or extend a backend implementation phase.

## Implemented state

- Independent uv project/environment/lockfile under testbed; original root/backend
  dependencies and application source unchanged. Resolved Ragas 0.4.4 and
  google-genai 3.5.0 only in the optional testbed judge dependency graph.
- HTTP-only client; no backend imports, SQL/Neo4j/Qdrant clients, admin routes,
  prompt changes, budget changes, or index activation/rebuild.
- Root canonical AGENTS.md now mandates all evaluation source/data/tests/docs and
  artifacts under /testbed. It explicitly allows Gemini judging only here and
  all-local Ollama embeddings. Root AGENTS remains ignored under the user's prior
  policy; no force-add, commit or push was performed.
- testbed/.env contains only the copied existing backend HTTP token/origin;
  no model key was copied, printed or committed. Core client never reads backend/.env.
- datasets/pilot.jsonl contains 18 literal-definition cases over all six contracts;
  exact original hashes/ranges verified. source_verified is not expert legal review.
- CLI: validate, preflight [--embeddings], collect, score, report. Chat generation
  requires --allow-live-chat; extractive control uses zero hosted generation.
- Capture identity pins dataset/case IDs, snapshot, provider profile, endpoint and
  mode. Case IDs/idempotency keys and run IDs survive retry; resume never resubmits
  a completed case. Writer lock prevents concurrent writers to one run directory.
- Deterministic metrics, actual-mode/error reporting, provenance checks and optional
  explicitly wired Gemini/Ragas + local Ollama adapters are implemented.
- generation_faithfulness remains unavailable: no exact model context receipt is
  exposed by the application. citation_groundedness_proxy is separately named.

## Files to read

1. README.md: installation and exact commands.
2. evaluation_workflow.md: dataset, metric formulas, judging/calibration and limits.
3. datasets/README.md and pilot.jsonl: actual cohort/reference labels.
4. reports/pilot_findings.md: measured initial findings and unresolved dependencies.
5. src/accordtrace_testbed/: client, metrics, capture, adapters and reports.

## Verification

Commands from repository root:

| Command/check | Actual result |
|---|---|
| python scripts/uv.py sync --project testbed --locked --extra judge --offline | Passed, resolver-generated lock; 105 packages resolved |
| python scripts/uv.py run --project testbed --offline accordtrace-testbed validate | 18 cases and all six original sources verified |
| python scripts/uv.py run --project testbed --extra judge --offline pytest -q -c testbed/pyproject.toml testbed/tests --basetemp=testbed/.runtime/pytest | 21 passed, expected Ragas legacy deprecation warnings |
| collect --run pilot_hybrid --mode retrieval | Real HTTP, 18 captures; all lexical fallback because local embedding service/model unavailable |
| collect --run pilot_exact --mode extractive --search-mode exact | Real HTTP, 17 complete / 18 planned; one ambiguous_term_selector |
| score/report for retained captures | JSON/Markdown written with denominators and pending/unavailable metrics |
| preflight | Backend search readiness true; snapshot/provider match; judge_configured=false; no hosted calls |
| --judge context_recall with missing testbed judge configuration | Fails closed into explicit pending scores; no Gemini request |
| Model/runtime checks | Ollama runtime 0.22.1 matches; exact model/digest not present in current model store |
| Source/docs/import/secret/ignore checks; git diff --check | Passed; no backend source import or HTTP token in proposed tracked files |

Unit tests exercise actual Ragas arithmetic with controlled verdicts and mock Gemini
scoring, not live Gemini quality. No live generative Groq chat was run in this pilot.
Source-only controls made zero model calls, verified in captured trace arrays.

## Actual results and service state

- Active snapshot a2c03b5f-c2cb-5798-b65e-aaf5fc025b63; existing source revision
  37d5c3b95d18dcd8404cc5ce3fd5069be062392f. No projections changed.
- Auto capture named pilot_hybrid: actually 18 lexical fallbacks; Recall@6 6/18;
  full-packet labelled evidence recovered in 14/18. Do not call it a hybrid result.
- Exact control: 17/18 cases completed, 106/106 citations valid. Conditional
  Recall@6 1.0 over 17, but all-planned required-evidence success 17/18.
- c1_definition_1 (Reverse Termination Fee) returns backend HTTP409
  ambiguous_term_selector. Error preserved, not repaired in the application.
- Docker Desktop and existing Compose services were started without rebuilding
  images or deleting data. Ollama serve was started with the installed executable;
  no model was pulled. Existing API/worker/stores and Ollama are left running.
- Runtime captures, source quotes, report JSON/Markdown, SDK cache and private
  configuration remain ignored under testbed/.runtime, .env, .venv and .cache.

## Remaining limitations and safe continuation

1. Restore the exact existing embeddinggemma:300m model/digest through an explicitly
   authorized operator action; verify runtime/model before a new hybrid cohort.
   Do not change the immutable backend profile or substitute another embedding space.
2. Configure TESTBED_GEMINI_API_KEY, explicit TESTBED_GEMINI_MODEL and confirmed
   free-tier flag only in testbed/.env. Never restore runtime backend Gemini.
3. Run a small scoring cohort, verify judge calibration with independent labels,
   and report pending/undefined/provider failures with their denominators.
4. Obtain reviewed negative, conditional/multi-hop, multi-turn and comparison cases
   before claiming full architecture quality. The current runner is single-turn.
5. Full generation faithfulness needs a verified actual-context export from the app;
   request separate backend authorization if that later becomes necessary.
6. Ragas API upgrades require renewed adapter/arithmetic checks; do not silently
   migrate deprecated metric names or accept embedding-provider defaults.

The initial harness is implemented and locally tested; full hybrid/generative and
live Gemini quality measurement is not complete. No evaluation benchmark accuracy,
expert legal correctness, production deployment or generalization is claimed.
