# Pilot dataset

Source: [The Atticus Project MAUD](https://huggingface.co/datasets/theatticusproject/maud), [Wang et al., EMNLP 2023](https://aclanthology.org/2023.emnlp-main.1019/). The source dataset lists CC BY 4.0. These source-derived excerpts retain agreement IDs, source revision and exact SHA-256/span provenance; this attribution does not assign a license to the application's code.

`pilot.jsonl`: 18 direct-definition diagnostics, not a held-out expert legal benchmark.

| Contract | Literal definitions selected |
|---|---|
| contract_0 | Business Day; Per Share Cash Amount; Code |
| contract_1 | Reverse Termination Fee; Antitrust Laws; Code |
| contract_2 | Business Day; Code; HSR Act |
| contract_3 | Business Day; Antitrust Laws; Code |
| contract_4 | Business Day; Closing Date; Code |
| contract_5 | Business Day; CA 2006; Code |

Revision: `37d5c3b95d18dcd8404cc5ce3fd5069be062392f`. Original source text files stay in the existing ignored data/maud location and are read-only.

The reviewed literal boundaries preserve the complete selected definition, including both conditional Reverse Termination Fee amounts and the payment-versus-filing Business Day distinction in contract_3. Code-like entries and printed page numbers must not absorb unrelated adjacent definitions. Exact offsets use decoded UTF-8/BOM-aware Unicode code points without platform newline translation.

`review_status=source_verified` records source/hash/offset alignment, not legal-expert review. A person may promote a case to human_reviewed only after actual independent adjudication. Generated gold answers never enter the backend index or its generation requests.

Preparation is reproducible with:
```powershell
python scripts/uv.py run --project testbed python testbed/scripts/prepare_pilot.py
```
It refuses to replace an existing dataset unless --overwrite is explicitly supplied. Do not overwrite later annotations merely to match new system outputs.

Validation:
```powershell
python scripts/uv.py run --project testbed accordtrace-testbed validate
```

Future cohorts should cover direct facts, scoped definitions, multi-hop references, exceptions, missing schedules, false premises, multi-turn provenance, and cross-agreement differences. Freeze separate tuning/held-out question families and keep contract-level counts visible. The current runner supports single-turn cases; multi-turn and full architecture ablations require explicit extensions.
