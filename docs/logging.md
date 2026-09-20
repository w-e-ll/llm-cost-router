# Logging and monitoring

## Configuration
Defaults: `LOG_LEVEL=INFO`, `LOG_FORMAT=text`. Set format to `json` for one JSON event per line.
Docker captures stdout and writes rotating files directly to `var/logs/api.log` and `var/logs/ui.log`.
Files rotate at 20 MiB with 10 backups. Docker stdout retention is separately 20 MiB / 5 files.

Formatting follows the example: UTC timestamp, PID, severity, module, event and fields.
Setup is idempotent across UI reruns and leaves framework/root handlers intact.
Container Uvicorn access logs are disabled because middleware logs requests without query strings.

## Events

| Event | Fields |
|---|---|
| http.request | Method, route template, status, duration, request ID |
| experiment.requested / budget_rejected / reused | Intent, budget rejection, duplicate handling |
| routing.selected / fallback | Task ID, policy, selected/primary/fallback model |
| model.attempt | Model, tokens, latency, estimated cost, failure/fatal flags |
| experiment.executed / failed | Status, policy costs, unknown usage |
| planning.projected | Run ID, volume, monthly costs and savings |
| db.run_claimed / run_conflict / run_saved | Reservation and persistence after commit |
| db.plan_saved | Source run and plan IDs |
| db.unavailable | Exception class only |
| simulation.compared | Explicit synthetic mode and savings |

The server generates `X-Request-ID` and returns it to the caller. Run IDs propagate into synchronous execution logs.
Unmatched routes omit arbitrary URL content. Business events belong to the API rather than being duplicated on every UI rerun.

Prompts, answers, tokens used for authentication, connection URLs, request bodies and provider bodies are not logged by business-event call sites.
This is not a universal scrubber for arbitrary future log messages: keep new event fields limited to operational metadata.
Unexpected provider exceptions are recorded as generic failures, without raw exception strings.

## Commands

```powershell
docker compose logs --follow --tail 100 api ui db
docker compose logs --since 10m api | Select-String 'routing.fallback|experiment.failed|db.unavailable'
Get-Content .\var\logs\api.log -Tail 100
Get-Content .\var\logs\ui.log -Tail 100
```

Health probes: API `/health` for liveness, `/health/db` for database/table readiness, Streamlit `/_stcore/health` for its process.
Filter routine health events by route in your log collector.

## Limits
Logs and health probes are available; metrics dashboards, alerts and OpenTelemetry are future work.
Use one worker per container when writing rotating files. Multi-worker deployments should use stdout-only collection or separate files.
