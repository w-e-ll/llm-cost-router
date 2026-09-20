from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpx
from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest

from llm_cost_router.api.main import app

UI_FILE = Path(__file__).resolve().parents[1] / "llm_cost_router" / "ui" / "app.py"


def test_live_is_landing_page_and_preflight_is_free():
    client = TestClient(app)
    with (
        patch("httpx.get", side_effect=lambda url, **kw: client.get("/api/v1/live/config")),
        patch(
            "httpx.post",
            side_effect=lambda url, **kw: client.post("/api/v1/live/estimate", json=kw["json"]),
        ),
    ):
        ui = AppTest.from_file(str(UI_FILE)).run(timeout=20)
        assert not ui.exception
        ui.button[0].click().run(timeout=30)
        assert not ui.exception
        assert not ui.error
        assert ui.radio[0].value == "Live OpenAI experiment"
        assert ui.session_state["live_prepared"]["estimate"]["within_budget"]
        assert not ui.metric


def test_ui_reports_api_unavailable():
    # Clear Streamlit's process-level cache before testing the failure path.
    import streamlit as st

    st.cache_data.clear()
    with patch("httpx.get", side_effect=httpx.ConnectError("offline")):
        ui = AppTest.from_file(str(UI_FILE)).run(timeout=20)
        assert not ui.exception
        assert "Cannot load live settings" in ui.error[0].value


def test_live_ui_prepare_run_and_retrieve_without_duplicate_calls(monkeypatch, repository):
    import json

    from llm_cost_router.api import live
    from llm_cost_router.core.live import DATASET, execute

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    calls = []

    def fake_execute(config, unused_client):
        def handler(request):
            payload = json.loads(request.content)
            calls.append(payload)
            answer = next(t["expected"] for t in DATASET if t["prompt"] == payload["input"])
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "usage": {"input_tokens": 100, "output_tokens": 10},
                    "output": [
                        {
                            "type": "message",
                            "content": [
                                {"type": "output_text", "text": json.dumps({"answer": answer})}
                            ],
                        }
                    ],
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as provider:
            return execute(config, provider)

    monkeypatch.setattr(live, "execute", fake_execute)
    client = TestClient(app)
    with (
        patch("httpx.get", side_effect=lambda url, **kw: client.get(urlsplit(url).path)),
        patch(
            "httpx.post",
            side_effect=lambda url, **kw: client.post(urlsplit(url).path, json=kw["json"]),
        ),
    ):
        ui = AppTest.from_file(str(UI_FILE)).run(timeout=20)
        ui.radio[0].set_value("Live OpenAI experiment").run(timeout=20)
        assert not ui.exception
        ui.button[0].click().run(timeout=20)
        assert calls == []
        next(b for b in ui.button if b.label == "Run paid OpenAI comparison").click().run(
            timeout=20
        )
        assert not ui.exception
        assert not ui.error
        assert len(calls) == 16
        assert len(ui.metric) == 3
        next(b for b in ui.button if b.label == "Run paid OpenAI comparison").click().run(
            timeout=20
        )
        assert len(calls) == 16
        assert ui.session_state["live_result"]["status"] == "completed"
        run_id = ui.session_state["live_result"]["run_id"]
        next(b for b in ui.button if b.label == "Use this run for planning").click().run(timeout=20)
        assert ui.radio[0].value == "Cost planning"
        assert ui.text_input[0].value == run_id
        next(b for b in ui.button if b.label == "Load run and calculate plan").click().run(
            timeout=20
        )
        assert not ui.exception
        assert not ui.error
        assert ui.session_state["cost_plan"]["source_run_id"] == run_id
        assert len(ui.metric) == 3
        assert len(calls) == 16
        # A bad subsequent ID must clear the prior results.
        ui.text_input[0].set_value("not-a-uuid")
        next(b for b in ui.button if b.label == "Load run and calculate plan").click().run(
            timeout=20
        )
        assert ui.error
        assert not ui.metric
