# Live OpenAI experiment — first run and video guide

Docker is the default deployment: follow the root README and [Docker operations](docker.md).
Host commands below are optional PyCharm development instructions. OpenAI remains external;
saved experiment data lives in Docker PostgreSQL after migration.

1. Set `OPENAI_API_KEY` in the FastAPI process environment (your PyCharm Run Configuration).
2. Restart FastAPI and refresh Streamlit. Select **Live OpenAI experiment**.
3. Keep baseline/complex/fallback `gpt-4.1-mini` and simple `gpt-4.1-nano`.
4. Click **Prepare experiment — no paid calls**. Review the selected models and reserve.
5. Click **Run paid OpenAI comparison** and wait for the results. Only this button starts paid work.
6. Inspect individual answers, then download the run JSON for the recording.
7. Click **Use this run for planning**. Adjust volume and budget, then click **Load run and calculate plan**.
   You can also open Cost planning directly and paste an older completed run ID. Planning uses
   that run's recorded models, token usage, costs and rates; it never makes paid calls.

Run `uv sync` and set up PostgreSQL as described in [the Windows guide](postgres-windows.md).
The HTTPX client calls OpenAI's Responses API; PostgreSQL persists runs and cost plans.
Account billing, model access and the key are verified by the first actual call, not by the
"key configured" indicator. That indicator only checks that the backend environment variable exists.

## Prices and budget

Published standard prices in USD per million tokens, checked 2026-09-17:

| Model | Input | Cached input | Output |
|---|---:|---:|---:|
| GPT-4.1 nano | 0.10 | 0.025 | 0.40 |
| GPT-4.1 mini | 0.40 | 0.10 | 1.60 |

Sources: [nano](https://developers.openai.com/api/docs/models/gpt-4.1-nano),
[mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

The default eight-task experiment makes 16 primary calls and at most four fallbacks. The default
budget guard is **$0.05 per experiment**, not a cumulative account limit. Output is capped at
128 tokens per call. Preflight allows 4,096 input tokens for each fixed short prompt and schema,
plus maximum output and possible fallback. It refuses a run if this reserve exceeds the selected
budget. This conservative application estimate is **not a provider-enforced billing cap**; actual
billing and changed prices must be checked with OpenAI. Price snapshot values are saved in each run.

There are no automatic HTTP retries. Authentication/rate-limit/provider/transport errors stop the
run. Calls without usage retain a separate reserve and are never treated as known-free calls.
Invalid/incomplete outputs with known usage can fall back once to the baseline model. Reference
answers never trigger fallback. Provider storage is disabled with `store=false`.

## What to show in the video

1. Architecture: Streamlit → FastAPI → OpenAI Responses API → PostgreSQL run snapshot.
2. Model selection, token prices and the free preflight reserve.
3. Run the same eight tasks under the two policies.
4. Compare actual reported tokens, calculated dollar cost, measured latency, correctness and errors.
5. Open the per-task answers. A cheap response that is wrong is not a successful optimization.
6. Explain limitations: predefined complexity labels, eight toy tasks, variable network latency,
   cache/order effects, dated rates, and no production quality or availability guarantee.

Accuracy is exact-match after trimming and case folding against explicit reference answers.
Latency measures client-observed request time and includes fallback. Nearest-rank p95 on eight
tasks is essentially the slowest request; it is not a stable production percentile. Monthly cost
projects the same workload mix and observed usage to the selected volume. It is not an invoice.
Partially aborted runs suppress savings and incomplete-policy monthly projections.

## Saved runs and recovery

Results live in PostgreSQL's `experiment_runs` table. Use the dashboard's download button to
retain a JSON copy. Repeating the same prepared run ID retrieves it without new paid calls.
Import legacy `var/live-runs/*.json` files with `uv run python -m llm_cost_router.db.import_runs`.
This is insert-only and never deletes the files. Listing IDs uses `GET /api/v1/live/runs`.
Prepare another experiment for a genuinely new run. Changed settings require a new ID.

If the UI disconnects, use **Retrieve prepared run — no paid calls** before starting another.
After refreshing the browser, use the recorded ID at `GET /api/v1/live/runs/{run_id}` in API docs.
A `started` result after a server crash is deliberately not auto-resumed; review account usage
before running again. Keep this unauthenticated development app bound to localhost.

## API

- `GET /api/v1/live/config`: available price profiles, key-present flag, defaults and dataset.
- `POST /api/v1/live/estimate`: free conservative reserve; never calls OpenAI.
- `POST /api/v1/live/run`: requires a UUID run ID and `confirm_paid: true`.
- `GET /api/v1/live/runs/{run_id}`: read saved results without provider calls.

Tests mock the provider but use actual PostgreSQL for database integration. No real OpenAI
requests are made by the automated test suite.
