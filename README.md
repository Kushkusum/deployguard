# DeployGuard+

An agentic canary-release gatekeeper for Kubernetes. Instead of promoting or
rolling back a canary deployment using static metric thresholds (the
industry-standard approach), a small multi-agent system reads live Prometheus
metrics, retrieves similar past incidents from a vector database (RAG), and
makes an explainable **promote / rollback / hold** decision with a written
justification — wired directly into a real GitHub Actions pipeline running
against a local Kubernetes cluster.

Everything here runs locally and for free: no paid APIs, no cloud billing,
an 8GB-RAM-friendly local LLM via Ollama.

## Why this, not another "AI-SRE" clone

The AIOps space (K8sGPT, HolmesGPT, KAgent, Aurora, and others) is already
crowded with tools that do generic anomaly-detect-and-fix. This project's
niche is narrower and more specific: **explainable release decisions with
incident memory**. The interesting part isn't "an AI watched my cluster" —
it's that every decision is grounded in retrieved precedent (RAG) and comes
with a justification a human can actually audit, closer to how real
production RAG-for-ops tools work than a black-box anomaly detector.

## Architecture

```text
git push to main
   |
   v
GitHub Actions (cloud runner: ubuntu-latest)
   - install deps, run pytest
   - docker build + push image to GHCR (versioned artifact record)
   |
   v
GitHub Actions (self-hosted runner: this machine)
   - docker build the same source locally (see "Why build twice" below)
   - k3d image import into the local cluster
   - kubectl rollout restart orders-canary
   - generate real traffic through the Service (from inside the cluster)
   |
   v
DeployGuard agent (Planner -> Executor -> Inspector)
   - Executor: pulls canary vs. stable latency/error signals from Prometheus,
     retrieves top-3 similar past incidents from ChromaDB (RAG)
   - Inspector: local LLM (Ollama, qwen2.5:1.5b) judges only the qualitative
     question - "does a retrieved incident describe a transient cause?" -
     while Python computes the actual latency ratio, error rate, and final
     decision deterministically (see "Why the LLM doesn't decide" below)
   |
   v
Pipeline branches on the decision:
   promote  -> kubectl scale canary to 9 replicas, stable to 1
   rollback -> kubectl rollout undo + reset BUG_MODE (see below)
   hold     -> no traffic shift, pipeline completes without acting
   |
   v
incident_report.md generated every run, uploaded as a build artifact
```

### The canary split

Two Kubernetes Deployments (`orders-stable`, `orders-canary`) share a single
label (`app: orders`) that one Service selects on. Neither Deployment's
`track` label is part of the Service's selector — so the Service
load-balances across whichever Pods exist under `app: orders`, and the
90/10 traffic split emerges purely from replica count (9 stable : 1 canary),
not from any special canary controller like Argo Rollouts or Flagger. This
is a deliberate simplification: every moving part is one `kubectl get` away
from being inspected, nothing is a black box.

### The BUG_MODE toggle

The `orders` service has a `BUG_MODE` environment variable that deliberately
triggers a reproducible regression: a 2-second sleep on `GET /orders`, and a
simulated exception on `POST /orders` once a shared counter hits a multiple
of 5. This exists so the "bad canary" scenario is controllable and
demoable on command, rather than hoping a real bug shows up during a demo.

## Repo structure

```text
deployguard/
  services/orders/       FastAPI + Redis app, Dockerfile, tests
  agent/
    executor.py           Prometheus queries + ChromaDB retrieval
    inspector.py           Local LLM call + deterministic decision logic
    planner.py              Minimal - see "what's underbuilt" below
    pipeline_gate.py         Entry point the CI pipeline actually calls
    seed_chroma.py            One-off script to (re)seed the incident corpus
    incidents/incidents.json   17 synthetic past-incident postmortems
  k8s/
    stable-deployment.yaml, canary-deployment.yaml, service.yaml, redis.yaml
    prometheus-config.yaml, prometheus-deployment.yaml, prometheus-rbac.yaml
  .github/workflows/pipeline.yaml
  docker-compose.yml        Local dev, no Kubernetes needed
```

## Setup

Requires: Docker Desktop (with a Linux/WSL2 backend), `kubectl`, `k3d`,
`ollama`, Python 3.10+. All installed via `winget` on Windows in this
project - see the workflow comments for exact package IDs if you need them.

```bash
# 1. Local dev loop, no Kubernetes yet
docker compose up --build
curl http://localhost:8000/health

# 2. Local Kubernetes cluster
k3d cluster create deployguard --agents 1
docker build -t orders:v1 ./services/orders
k3d image import orders:v1 -c deployguard
kubectl apply -f k8s/redis.yaml -f k8s/stable-deployment.yaml \
  -f k8s/canary-deployment.yaml -f k8s/service.yaml
kubectl apply -f k8s/prometheus-rbac.yaml -f k8s/prometheus-config.yaml \
  -f k8s/prometheus-deployment.yaml

# 3. The agent
ollama pull qwen2.5:1.5b
ollama pull nomic-embed-text
pip install -r agent/requirements.txt
python agent/seed_chroma.py
kubectl port-forward svc/prometheus-svc 9090:9090 &
python agent/run_agent.py       # standalone test, not wired into CI yet

# 4. The real pipeline
# Register a self-hosted GitHub Actions runner (Settings -> Actions -> Runners)
# on this machine, then push to main. The pipeline builds, tests, deploys the
# canary, runs the gate, and branches on its decision automatically.
```

## Running the demo yourself

```bash
# Clean scenario (expect: promote)
kubectl set env deployment/orders-canary BUG_MODE=false
# push any trivial change to main

# Regression scenario (expect: rollback)
kubectl set env deployment/orders-canary BUG_MODE=true
# push any trivial change to main
```

Watch the repo's **Actions** tab. The `deploy-canary` job's steps show the
gate's decision and which of promote/rollback/hold actually ran; the
`incident-report` artifact has the full reasoning trail.

## Engineering decisions and what went wrong

This section exists because the interesting part of building this wasn't
writing YAML that worked on the first try — it was the parts that didn't,
and why.

**RBAC worked on the first attempt.** Prometheus's `kubernetes_sd_configs`
needs a ServiceAccount + ClusterRole + ClusterRoleBinding to list Pods; all
10 Pods (9 stable, 1 canary) were auto-discovered and healthy immediately
after applying it.

**Prometheus wasn't exposing the `track` label at all.** The `relabel_configs`
used the pod's `track` label to decide *which* Pods to scrape, but never
promoted it into an actual exposed metric label — so `{track="canary"}`
queries silently returned empty results. Fixed with one additional
`relabel_configs` rule (`action: replace, target_label: track`). Caught by
directly querying Prometheus before trusting the Executor's code, not by
reading the code and assuming it was right.

**The Inspector went through three failed prompt-engineering attempts before
the real fix.** A 1.5B local model asked to both compare two numbers *and*
decide promote/rollback/hold kept failing at the last step even when every
intermediate fact was extracted correctly — including once answering its own
explicit yes/no threshold checks correctly, then still stating the opposite
final decision. The fix wasn't a better prompt; it was moving the arithmetic
and the final decision into deterministic Python, and narrowing the LLM's
job to the one genuinely qualitative question it's actually suited for: does
a retrieved incident's narrative plausibly explain the current signals as a
transient cause. This is the single most important design decision in the
project — a small local model is a capable judge of *fuzzy semantic
similarity*, and an unreliable judge of *arithmetic and multi-step logic*.

**Four infrastructure bugs surfaced only by testing the CI pipeline for
real, not by reading the YAML:**

1. GHCR requires lowercase repository names; `github.repository_owner`
   preserves the account's actual casing, breaking the image tag.
2. Docker Desktop's containerd image store loses track of layer content for
   `docker pull`-ed (as opposed to locally-built) images, breaking both
   `docker save`/`k3d image import` and even a plain `docker push` to a
   local registry. Fixed by building the image locally on the self-hosted
   runner instead of pulling GHCR's copy — GHCR still gets the cloud-built
   image as the canonical versioned record, it's just not the deployment
   source.
3. `kubectl set image` is a no-op when the tag string is unchanged across
   runs (true here every time - the canary is always tagged
   `orders:canary-latest`), even though the underlying image content is new
   each run. Confirmed directly: rebuilt the image with different content,
   ran `set image`, and the pod's name/revision never changed. Fixed with an
   explicit `kubectl rollout restart` immediately after.
4. The gate's first real run "succeeded" but silently evaluated zero traffic
   - Prometheus counters reset on every pod restart, and nothing else sends
   requests to the service - so every signal defaulted to 0, which reads as
   perfectly healthy regardless of the canary's actual state. Fixed by
   generating real traffic through the Service (from inside the cluster,
   not via `kubectl port-forward`, which pins to a single backing Pod and
   never actually load-balances against a Service) immediately before the
   gate runs.

**`kubectl rollout undo` alone doesn't make rollback meaningful in this
project's design.** The deploy step's `rollout restart` runs unconditionally
on every push, so by the time the gate decides, the "previous revision"
undo targets is often just another restart-only copy of the same bad state,
not the last known-good one. Underneath that, the canary's image tag is
always the same mutable string, so image-level rollback can't reference
genuinely older content even in principle once a newer build has overwritten
it locally. Since this project's regressions are represented by the
`BUG_MODE` toggle (a deliberate, controllable stand-in for a real code
regression - see above), the rollback step also resets `BUG_MODE` directly,
alongside running the real `rollout undo` for what it's worth on the pod
template itself. **This is an honest scope limitation, not a hidden one**: a
production system would need unique, immutable image tags per build for
rollback to be substantive rather than symbolic.

**Windows-specific friction, worth knowing if you try this on Windows too:**
`k3d`'s winget package doesn't create the shim its own installer expects
(the `WinGet\Links` folder was empty), so a freshly-opened shell never had
it on PATH — every command needed an explicit
`[System.Environment]::GetEnvironmentVariable(...)` reload. The self-hosted
runner inherited that same stale PATH when first started, breaking every
`k3d` call inside CI. Fixed durably with a `.env` file in the runner's root
directory (GitHub's documented mechanism for per-job environment variables)
containing the full, correct PATH - independent of whatever shell happens to
launch the runner next.

## Known limitations

- **Image tags are not content-addressed.** `orders:canary-latest` is reused
  every build; a production version of this needs per-commit tags (or
  digest pinning) for rollback to be meaningful at the image level, not just
  via the `BUG_MODE` stand-in.
- **The Planner is a stub.** For a single service with three fixed checks,
  there isn't much for a planning step to decide. Its value would show up
  with more services or more heterogeneous check types - it's included now
  so the pattern is correct and extensible, not because it's doing real work
  today.
- **The incident corpus is synthetic** (17 hand-written postmortems), not
  real production history. The retrieval mechanism is real RAG; the memory
  it's drawing on is illustrative.
- **Zero-cost and local-first by design**, which means: one small local LLM
  (not a frontier model), a hand-rolled canary split (not Argo
  Rollouts/Flagger), and a self-hosted GitHub Actions runner (because a
  cloud-hosted runner has no way to reach a cluster that only exists on this
  laptop).
