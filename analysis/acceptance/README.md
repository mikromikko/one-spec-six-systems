# Recheck the six existing outputs

These are the original author's HTTP acceptance adapters, recovered from the working copy in October 2026. Its six result files and the counted output trees match the public `b3394ba8286680a75b6031181430d93edb061d98` snapshot byte-for-byte. `provenance.json` records SHA-256 hashes of the recovered originals and 67 published input files. This recovery is by the original publisher, not an independent replication.

The scoring and requests are preserved. Portability changes replace an operator-specific home-directory path with `runs/`, constrain HTTP calls to `127.0.0.1` with redirects/proxies disabled, replace an unsafe temporary cookie filename with an allocated file, and write fresh UTC-dated receipts and full unit-test logs exclusively to an explicit directory. Check 10 now describes unit-test execution without asserting that the original README startup instructions worked verbatim. The helper, local startup wrapper and orchestration are documented here; the generated systems and July results are unchanged.

## Bounded container rerun

Docker is required. Read `Dockerfile`, `rerun.py` and the six `check-*.py` adapters before executing them. Image preparation downloads Ubuntu, npm and Python dependencies without credentials. The base image is pinned by digest; Ubuntu package updates and host architecture are recorded runtime differences, rather than a reconstruction of the July machine. The runtime has no network interface except loopback, no published ports, a read-only root filesystem, disposable working storage, and a single empty receipt-directory mount. It neither invokes the model-generation `run.sh` nor mounts your home directory, agent settings or credentials.

From the repository root:

```sh
python3 -m unittest discover -s analysis/acceptance -p test_helpers.py -v

docker build -f analysis/acceptance/Dockerfile -t research-acceptance .

# Use a NEW empty directory for every run. Do not point this at analysis/.
RECEIPTS=$(mktemp -d "$PWD/acceptance-receipts.XXXXXX")

docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --cpus 2 --memory 1g \
  --tmpfs /tmp:rw,nosuid,nodev,size=256m \
  --tmpfs /work:rw,nosuid,nodev,exec,size=768m \
  --env ACCEPTANCE_ISOLATED=1 \
  --mount type=bind,src="$RECEIPTS",dst=/receipts \
  research-acceptance
```

Each service starts sequentially from the existing pinned output. The first two Node services are seeded; the third seeds itself. The Node preload constrains fixed service ports in the unchanged startup files to `127.0.0.1`. Unit suites retain their native ephemeral binding because Supertest requires a synchronous listener address; the container has no interface except loopback. FastAPI uses its published module with `--host 127.0.0.1` and without development reload. The two standard-library Python services already bind loopback. Dependencies are installed with each published npm lockfile (`npm ci --ignore-scripts`) and `python-rerun.lock` (a complete October dependency freeze satisfying the published top-level requirements). Service readiness is bounded to 20 seconds and each adapter to 360 seconds. The receipt records commands, actual runtime versions, startup failures, scores and before/after input-hash verification. Unit tests run inside the same isolated container.

An individual adapter also accepts `--base http://127.0.0.1:PORT[/api] --output-dir /absolute/fresh/receipts`, but it sends writes and deletions to that service. Use disposable isolated data. On macOS, place this directory under a path shared with your Docker VM (the repository path works with Colima); only this empty directory is mounted. Existing receipts are never overwritten. Exit zero means the adapter completed; consult its pass/partial/fail rows rather than treating completion as full acceptance.

## Evidence boundary

The July `analysis/results-*.md` files remain the historical receipts. New receipts live under `analysis/reruns/` and identify runtime/startup changes and any differing outcomes. These ten HTTP checks establish only the behavior they exercise. In particular, a pass on an append-only API does not establish database immutability, and successful role checks do not establish complete object-level authorization. The [July security review](../../security-review-2026-07-18.md) remains applicable: none of these outputs is safe to deploy as shipped.

This rerun generates no new model outputs, measures no independent reviewer effort, and cannot estimate a population failure rate or rank the tools. It does not test Taiga as an experimental arm. Fresh results must be reported separately from the six original draws.

The [11 October 2026 completed rerun](../reruns/2026-10-11/README.md) includes the raw scores, full unit logs, input hashes and the one historical manual-scoring difference.
