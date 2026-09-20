import json
from uuid import uuid4

import httpx
import streamlit as st


def use_run_for_planning(run_id):
    st.session_state["planning_run_id"] = run_id
    st.session_state.pop("cost_plan", None)
    st.session_state["view"] = "Cost planning"


def render_result(result):
    if result.get("status") in ("started", "failed"):
        st.info(
            "Run is still active or was interrupted. Retrieve it later using the same run ID; "
            "it will not be charged again automatically."
        )
        return
    st.caption(f"Saved run {result['run_id']} · {result['created_at']} · {result['status']}")
    if result["status"] == "aborted":
        st.warning(
            "Run stopped after an API or usage-accounting error. Check per-task details. "
            "Incomplete runs do not show savings or full-run projections."
        )
    summaries = result["summaries"]
    a, b, c = st.columns(3)
    a.metric("Baseline estimated cost", f"${float(summaries['baseline']['known_cost_usd']):.6f}")
    b.metric("Routed estimated cost", f"${float(summaries['routed']['known_cost_usd']):.6f}")
    savings = result["savings_usd"]
    c.metric("Cost difference", f"${float(savings):.6f}" if savings is not None else "Incomplete")
    st.dataframe(list(summaries.values()), hide_index=True, width="stretch")
    st.caption(
        "Costs use actual reported tokens and published rates. Unknown-usage calls are "
        "shown separately. Monthly figures are projections. p95 is measured on a tiny sample."
    )
    st.subheader("Answers and evidence")
    st.dataframe(
        [
            {
                "Task": row["id"],
                "Policy": row["policy"],
                "Models": " → ".join(a["model"] for a in row["attempts"]),
                "Expected": row["expected"],
                "Answer": row["answer"],
                "Correct": row["correct"],
                "Latency ms": row["latency_ms"],
                "Error": row["error"],
            }
            for row in result["rows"]
        ],
        hide_index=True,
        width="stretch",
    )
    with st.expander("Experiment settings and limitations"):
        st.json(result["config"])
        for note in result["notes"]:
            st.write(note)
    st.download_button(
        "Download live results",
        json.dumps(result, indent=2),
        file_name=f"live-{result['run_id']}.json",
        mime="application/json",
    )
    if result["status"] == "completed":
        st.button(
            "Use this run for planning", on_click=use_run_for_planning, args=(result["run_id"],)
        )


def render_live(api_url):
    st.subheader("Live OpenAI experiment")
    try:
        response = httpx.get(f"{api_url}/api/v1/live/config", timeout=5)
        response.raise_for_status()
        info = response.json()
    except (httpx.HTTPError, ValueError):
        st.error("Cannot load live settings. Restart the updated FastAPI server.")
        return
    configured = info["key_configured"]
    st.caption(
        "OpenAI key: configured in backend" if configured else "OpenAI key: missing in backend"
    )
    st.caption(f"USD per 1M tokens · price snapshot {info['price_date']}")
    st.dataframe(
        [{"Model": name, **rates} for name, rates in info["models"].items()],
        hide_index=True,
        width="stretch",
    )
    names = list(info["models"])
    with st.form("live_settings"):
        a, b, c = st.columns(3)
        baseline = a.selectbox("Baseline / fallback model", names, index=1)
        simple = b.selectbox("Simple-task model", names)
        complex_model = c.selectbox("Complex-task model", names, index=1)
        count = a.selectbox("Number of tasks (each runs twice)", [2, 4, 6, 8], index=3)
        budget = b.number_input(
            "Per-run budget guard (USD)", 0.0, 1.0, 0.05, step=0.01, format="%.2f"
        )
        sla = c.number_input("Latency SLA (ms)", 1.0, 120000.0, 5000.0)
        monthly = a.number_input("Monthly requests for projection", 1, 10000000, 100000)
        fallback = b.checkbox("Fallback on invalid/incomplete response", value=True)
        preview = st.form_submit_button("Prepare experiment — no paid calls")
    if preview:
        st.session_state.pop("live_result", None)
        st.session_state.pop("live_prepared", None)
        config = {
            "baseline_model": baseline,
            "simple_model": simple,
            "complex_model": complex_model,
            "fallback_enabled": fallback,
            "task_count": count,
            "budget_usd": str(budget),
            "sla_ms": sla,
            "monthly_requests": monthly,
        }
        try:
            response = httpx.post(f"{api_url}/api/v1/live/estimate", json=config, timeout=10)
            response.raise_for_status()
            st.session_state["live_prepared"] = {
                "config": config,
                "estimate": response.json(),
                "run_id": str(uuid4()),
            }
        except httpx.HTTPError:
            st.error("Preflight failed. Check the API and input values.")
    prepared = st.session_state.get("live_prepared")
    if prepared:
        st.subheader("Prepared experiment")
        st.json(prepared["config"], expanded=False)
        estimate = prepared["estimate"]
        st.write(
            f"Conservative reserve: ${float(estimate['reserved_usd']):.4f}; "
            f"at most {estimate['max_calls']} API calls, including possible fallbacks."
        )
        st.caption(
            "This applies to the prepared settings above. Prepare again after edits. "
            "The guard is an application estimate, not a provider billing cap."
        )
        if not estimate["within_budget"]:
            st.warning("The reserve exceeds your budget. Reduce the task count and prepare again.")
        if st.button(
            "Run paid OpenAI comparison",
            type="primary",
            disabled=not configured or not estimate["within_budget"],
        ):
            st.session_state.pop("live_result", None)
            try:
                with st.spinner("Calling OpenAI for both policies. This may take a few minutes…"):
                    response = httpx.post(
                        f"{api_url}/api/v1/live/run",
                        json={
                            **prepared["config"],
                            "run_id": prepared["run_id"],
                            "confirm_paid": True,
                        },
                        timeout=600,
                    )
                    response.raise_for_status()
                    st.session_state["live_result"] = response.json()
            except httpx.HTTPStatusError as exc:
                try:
                    detail = exc.response.json().get("detail", "Request failed")
                except ValueError:
                    detail = "Request failed"
                st.error(str(detail))
            except httpx.HTTPError:
                st.error("Connection interrupted. Retrieve the run below before preparing another.")
        st.caption(f"Run ID: {prepared['run_id']}. Repeating this ID retrieves the saved result.")
        if st.button("Retrieve prepared run — no paid calls"):
            try:
                response = httpx.get(f"{api_url}/api/v1/live/runs/{prepared['run_id']}", timeout=10)
                response.raise_for_status()
                st.session_state["live_result"] = response.json()
            except httpx.HTTPError:
                st.error("Run not found or API unavailable.")
    if "live_result" in st.session_state:
        render_result(st.session_state["live_result"])
    with st.expander("Test dataset — prompts and reference answers"):
        st.dataframe(info["dataset"], hide_index=True, width="stretch")
        st.caption(
            "Task complexity is predefined. Reference answers are used for evaluation only. "
            "Transport errors stop the experiment; wrong answers do not trigger fallback."
        )
