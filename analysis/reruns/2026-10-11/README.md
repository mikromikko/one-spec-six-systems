# Existing-output acceptance rerun — 11 October 2026 UTC

The six counted outputs from snapshot `b3394ba8286680a75b6031181430d93edb061d98` were rechecked with recovered author-working-copy adapters. No model was called and no generated source was edited. All 67 published input hashes matched before and after execution. All six adapters completed, and all 72 authored unit tests passed. The successful bounded runtime lasted 52.4 seconds, excluding image preparation and adapter restoration. This is an original-publisher verification, not independent replication or a measurement of reviewer effort.

Scores below are pass/partial/fail counts. Fresh raw results are preserved; they have not been manually regraded.

| Output | July published score | October raw automated score | Passing unit tests |
|---|---|---|---|
| claude-run1 | 9/1/0 | 9/1/0 | 19 |
| claude-run2 | 10/0/0 | 10/0/0 | 16 |
| claude-run3 | 9/1/0 | 9/1/0 | 14 |
| copilot-run1 | 9/1/0 | 9/1/0 | 7 |
| copilot-run2 | 10/0/0 | 9/1/0 | 8 |
| copilot-run3 | 10/0/0 | 10/0/0 | 8 |

Five score vectors match the July receipts. Copilot run2 differs only on check 6: both executions returned `501` for audit-log PUT and DELETE. The July receipt explicitly says this was **regraded from partial to pass by human judgment**, because the capability was not implemented. The recovered script still treats `501` as partial. The rerun preserves that automated result and the original published human judgment separately. This is a scoring-layer difference, not new evidence that the API became mutable. Applying the already recorded July judgment gives the original aggregate; the raw October aggregate is 56 pass / 4 partial / 0 fail.

`receipt.json` records actual runtimes, commands, adapter hashes and deviations; `isolation.json` records the inspected container isolation and image ID. Python transitive dependencies are frozen in `python-dependencies.txt` and `../../acceptance/python-rerun.lock`. The original npm lockfiles are used. Node 24.18.1, Python 3.12.3 and aarch64 Linux differ from the original host. Service startup uses fixed loopback ports, FastAPI without development reload, and fresh disposable data; these checks do not assert verbatim original README startup.

Preparation exposed a Docker path-sharing issue, omission of tracked bytecode by an initial ignore rule, and a Supertest incompatibility in an initial all-port loopback preload. Those attempts were stopped before a complete scoring receipt and were not counted as generated-system failures. The final recipe includes all pinned files and constrains only fixed Node service ports; unit-test ephemeral listeners retain their native behavior in the container's loopback-only network. The fixed-port and Supertest diagnostic passed before the completed run.

The acceptance checklist has limited coverage. Its passing checks do not overturn the [source-assisted July security review](../../../security-review-2026-07-18.md): none of the six outputs is safe to deploy as shipped. This rerun does not test Taiga, estimate population failure rates, rank tools, generate independent draws, or establish independent reviewer effort.
