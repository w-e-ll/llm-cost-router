import json
from uuid import UUID

import httpx
import streamlit as st


def render_planning(api_url):
    st.subheader("Cost planning from a live experiment")
    st.caption(
        "Paste a saved run ID, or select ‘Use this run for planning’ in live results. "
        "Loading and planning make no paid calls."
    )
    with st.form("planning"):
        run_id = st.text_input("Source run ID", key="planning_run_id")
        a, b, c = st.columns(3)
        volume = a.number_input("Requests / month", 0, 10000000, 100000, step=1000)
        days = b.number_input("Days / month", 1, 31, 30)
        budget = c.number_input("Monthly budget (USD)", 0.0, 1000000.0, 100.0)
        submitted = st.form_submit_button("Load run and calculate plan", type="primary")
    if submitted:
        st.session_state.pop("cost_plan", None)
        try:
            normalized = str(UUID(run_id.strip()))
        except ValueError:
            st.error("Enter a valid run ID from a saved live experiment.")
            return
        try:
            response = httpx.post(
                f"{api_url}/api/v1/planning",
                json={
                    "run_id": normalized,
                    "monthly_requests": volume,
                    "days_per_month": days,
                    "monthly_budget_usd": str(budget),
                },
                timeout=15,
            )
            response.raise_for_status()
            st.session_state["cost_plan"] = response.json()
        except httpx.HTTPStatusError as exc:
            messages = {
                404: "Run not found. Check the ID and the FastAPI server's saved runs.",
                409: "This run is not complete. Use a completed live experiment.",
                422: "Run or planning settings are invalid; complete token/cost data is required.",
            }
            st.error(messages.get(exc.response.status_code, "Could not load this run."))
        except (httpx.HTTPError, ValueError):
            st.error("Cannot reach the planning API. Restart the updated FastAPI server.")
    plan = st.session_state.get("cost_plan")
    if not plan:
        return
    st.divider()
    st.caption("Results use the last submitted settings. Submit again after editing.")
    st.write(f"Source run: `{plan['source_run_id']}`")
    if plan.get("plan_id"):
        st.caption(f"Saved PostgreSQL plan ID: {plan['plan_id']}")
    st.caption(
        f"Recorded {plan['source_created_at']} · prices {plan['price_date']} · "
        f"simple tasks {plan['simple_fraction']:.0%}"
    )
    baseline, routed = plan["policies"]
    a, b, c = st.columns(3)
    a.metric("Projected baseline / month", f"${float(baseline['monthly_cost_usd']):,.2f}")
    b.metric("Projected routed / month", f"${float(routed['monthly_cost_usd']):,.2f}")
    c.metric("Projected cost difference", f"${float(plan['projected_savings_usd']):,.2f}")
    st.subheader("Projected costs and budget coverage")
    st.dataframe(
        [
            {
                "Policy": p["policy"],
                "Models": ", ".join(p["models"]),
                "$ / request": float(p["cost_per_request_usd"]),
                "$ / day": float(p["daily_cost_usd"]),
                "$ / month": float(p["monthly_cost_usd"]),
                "Within budget": p["within_budget"],
                "Budget gap ($)": float(p["monthly_budget_gap_usd"]),
                "Estimated requests affordable": p["estimated_requests_affordable"],
            }
            for p in plan["policies"]
        ],
        hide_index=True,
        width="stretch",
    )
    st.subheader("Source measurements — not forecasts")
    st.dataframe(
        [
            {
                "Policy": p["policy"],
                "Sample tasks": p["sample_tasks"],
                "Avg input tokens / task": p["avg_input_tokens"],
                "Avg output tokens / task": p["avg_output_tokens"],
                "Avg cached tokens / task": p["avg_cached_tokens"],
                "Sample accuracy (%)": p["sample_accuracy_percent"],
                "Sample p95 (ms)": p["sample_p95_ms"],
                "Sample errors": p["sample_errors"],
                "Sample fallbacks": p["sample_fallbacks"],
            }
            for p in plan["policies"]
        ],
        hide_index=True,
        width="stretch",
    )
    with st.expander("Source models, rates and planning assumptions"):
        st.json(plan["source_config"])
        st.json(plan["source_prices"])
        for assumption in plan["assumptions"]:
            st.write(assumption)
    st.download_button(
        "Download cost plan",
        json.dumps(plan, indent=2),
        file_name=f"plan-{plan['source_run_id']}.json",
        mime="application/json",
    )
