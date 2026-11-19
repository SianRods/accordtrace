# Automatic evaluation PDF reports

## Configuration check

Run from the repository root:

```powershell
python scripts/uv.py sync --project testbed --locked --extra judge
python scripts/uv.py run --project testbed accordtrace-testbed config
```

The `config` command validates effective settings offline. It reports secret
presence as booleans and never prints credential values. It does not verify a key
with Gemini, inspect live free-tier eligibility, or check installed Ollama weights.
Use `preflight --embeddings` for the existing backend/local embedding checks.

The local `.env` was checked on 18 November 2026: backend token and Gemini key
are present; the configured judge is `gemini-2.5-flash-lite`, with free-tier
confirmation enabled. Local Ollama is configured for `embeddinggemma:300m`.
Missing source-directory, budget and timeout entries were populated with the
documented defaults without changing existing values. This is configuration
verification only. No hosted calls were made for PDF work.

All supported configuration keys are documented in `.env.example`:

| Keys | Purpose |
|---|---|
| `TESTBED_BACKEND_URL`, `TESTBED_BACKEND_API_TOKEN` | HTTP application under test; token required for collection/preflight |
| `TESTBED_SOURCE_DIR` | Read-only original MAUD source directory for reference validation |
| `TESTBED_OLLAMA_URL`, `TESTBED_OLLAMA_MODEL` | Local-only evaluation embeddings; must match captured backend profile |
| `TESTBED_GEMINI_API_KEY`, `TESTBED_GEMINI_MODEL`, `TESTBED_GEMINI_FREE_TIER_CONFIRMED` | All three required for opt-in live judging |
| `TESTBED_JUDGE_REQUESTS_PER_MINUTE`, `TESTBED_JUDGE_MAX_CALLS` | Conservative physical-call budgets, defaults 2 and 40 |
| `TESTBED_REQUEST_TIMEOUT_SECONDS`, `TESTBED_CHAT_WAIT_SECONDS` | Defaults 30 and 660 seconds |

PDF output has no API key, external font, renderer, or additional environment
variable requirement. Generation uses ReportLab and its bundled fonts. Optional
Ragas/Google packages are only needed for judge work, not offline reporting.

## End-of-run outputs

`collect`, `score` and `report` write the same three files in
`testbed/.runtime/<run>/`:

- `report.json`: structured aggregate measurements and provenance.
- `report.md`: readable Markdown summary.
- `report.pdf`: standard A4 presentation generated from the structured report.

The PDF uses numbered pages, consistent type, repeating table headings, overview,
metric results, interpretations, coverage/status reasons, outcomes, per-contract
tables, limitations and run identity. No source quotes or credentials are added
by the reporting layer. The run identity includes source/dataset fingerprints,
snapshot and embedding profile; judge profiles appear when captured in scores.

PDF generation is synchronous, local and network-free. It requires no evaluation
rerun or model call. Small pilot reports ordinarily regenerate within seconds;
time grows with report size. Completed PDF files are atomically replaced.
The CLI holds the run's writer lock while collecting/scoring and generating output.

If collection/scoring exits with an error after creating a valid manifest, the CLI
attempts to generate a partial report in its `finally` block. Failures and missing
cases stay visible. An invalid/missing manifest cannot yield a meaningful report.
Forced process termination, disk errors or PDF rendering errors can prevent final
output; close any viewer locking the PDF and regenerate it with `report`.

```powershell
# Existing saved observations/scores only; no provider calls or backend requests.
python scripts/uv.py run --project testbed accordtrace-testbed report --run pilot_exact

# Collection writes its PDF automatically; existing collection safety rules apply.
python scripts/uv.py run --project testbed accordtrace-testbed collect --run my_pilot --mode retrieval

# Explicit live judge opt-in; PDF automatically includes new scores or pending reasons.
python scripts/uv.py run --project testbed --extra judge accordtrace-testbed score --run my_pilot --judge --metrics context_recall
```

Existing captured pilot reports retain historical failures and missing judge
configuration reasons. Adding credentials now does not retroactively score a saved
run. Use an explicit judge invocation when ready; a PDF refresh never scores it.

## Template and future metrics

Edit `reports/pdf_template.json` to change the title, subtitle, accent color,
footer and metric explanations. Plain strings are escaped, not evaluated as code
or markup. The template is version-controlled; generated PDFs remain ignored.

The renderer discovers **every key** in `report.json.metrics` and **every statistic**
under each metric. There is no fixed list of metrics in PDF generation. New means,
sample sizes, confidence intervals or other statistics appear automatically.
Extra top-level report metadata and per-contract metric fields are also discovered.
Large metric schemas use expanded tables rather than excessive column counts.

For example, a metric producer can add:

```json
{
  "metrics": {
    "new_retrieval_metric": {
      "mean": 0.72,
      "scored_cases": 15,
      "observed_cases": 18,
      "confidence_interval": [0.55, 0.86]
    }
  }
}
```

It appears without changing the renderer. Optionally add a matching key to the
template's `metrics` object with `label` and `interpretation`. Labels are used in
expanded layouts; raw metric keys remain visible for reproducibility. Unknown
metrics have no assumed unit, higher/lower direction, or acceptance threshold.

Adding a metric to the actual evaluation pipeline still requires implementing
and testing its calculation and aggregation. The renderer does not invent a
formula, call a provider or compute a metric merely because a template lists it.
`report` rebuilds JSON from saved observations/scores; hand-editing generated JSON
is not a durable way to register new metrics.

## Interpretation rules

- `mean` is conditional on `scored_cases`, not all planned questions. Always read
  planned, observed and complete counts alongside it.
- Null is **Not measured**, non-finite values are **Undefined**. Neither is zero.
  Pending/unavailable/not-applicable metrics appear in judge coverage and reasons.
- `1.0000` means 100% only for proportions. Counts keep their own units.
- Citation integrity is mechanical evidence consistency, not semantic correctness.
- Citation groundedness is a proxy. Actual generation faithfulness remains unavailable
  until exact model-visible context is exposed through the application API.
- Lexical fallback warnings prevent interpreting an auto search run as hybrid quality.
- Historical judge scores may use earlier configuration. Read captured judge profiles,
  not the current `.env`, to identify the judge that produced a result.

## Verification

Run the full independent suite:

```powershell
python scripts/uv.py run --project testbed --extra judge pytest -q -c testbed/pyproject.toml testbed/tests --basetemp=testbed/.runtime/pytest
```

PDF tests inspect actual extracted text, future metric/statistic coverage, escaping,
null display, long/paginated tables, automatic output, atomic replacement and secret
redaction. Ragas arithmetic/provider-isolation tests remain separate from model
quality measurements. Visual inspection is also required when changing layout.

The two existing pilot PDFs were rendered locally and visually inspected. System
MiKTeX `pdftoppm` is unconfigured, so the independent dev dependency `pypdfium2`
was used instead. Previews stay under `testbed/.runtime/pdf_preview/` and are not
needed by normal report generation. No backend code or dependencies were changed.
