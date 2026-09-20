# Technical decisions

## 1. Docker Compose
**Decision:** Separate UI, API, database and one-shot migration services, sharing an app image.
**Why:** Run all Python dependencies without host Python or PostgreSQL.
**Tradeoff:** Docker Desktop is required. OpenAI remains an external paid API.

## 2. PostgreSQL snapshots
**Decision:** JSONB snapshots with typed UUID/status/timestamp columns; plans reference their source run.
**Why:** Preserve the exact rates, usage and assumptions behind calculations.
**Tradeoff:** Large-scale analytics may require normalized attempt tables. No vector search is needed; pgvector is omitted.

## 3. Database permissions
**Decision:** Non-superuser application role; separate test database. Admin password is not given to app containers.
**Tradeoff:** The app role owns tables to run migrations. Production should separate migration and runtime roles.

## 4. Reserve before paid execution
**Decision:** Commit a unique run ID before provider calls; do not overwrite final results.
**Why:** Repeated submissions must not duplicate paid work.
**Tradeoff:** Crashed runs can remain started and need reconciliation. The process lock is not a distributed scheduler.

## 5. Observed-cost planning
**Decision:** Plan from completed experiment snapshots using Decimal arithmetic and recorded rates.
**Tradeoff:** Extrapolation assumes unchanged workload, tokens and cache patterns. It is not an invoice.

## 6. Bounded fallback, no HTTP retries
**Decision:** Reserve potential attempts, allow one eligible operational fallback, stop when usage is unknown.
**Tradeoff:** Conservative reserves can reject affordable runs. The guard is not a provider billing cap.

## 7. Centralized logging
**Decision:** Follow the example's timestamp/PID/level/module format; add request/run IDs, stdout, rotating files and optional JSON.
**Why:** Diagnose routing, provider attempts, costs and persistence without recording secrets or content.
**Tradeoff:** Logs and health checks are implemented; traces, alerts and metrics dashboards are future work. One process owns each rotating file.

## 8. Verify before native removal
**Decision:** Dump, restore and compare complete saved records before deleting project-local native PostgreSQL.
**Tradeoff:** Named volumes and local dumps still need off-machine backups. The helper never runs `down -v`.

## 9. Frozen dependencies and versioning
**Decision:** uv.lock drives images; VERSION.txt, pyproject and API share a version.
**Tradeoff:** Base images are version-tagged, not digest-pinned. Rebuild and test upgrades. No duplicate requirements.txt.

## 10. Small experiment scope
**Decision:** No Redis, Kubernetes or extra infrastructure without a concrete need.
**Tradeoff:** No multi-user authorization or production SLO guarantee. Eight fixed tasks do not establish general model quality.

