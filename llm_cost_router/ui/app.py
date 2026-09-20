"""Streamlit HTTP client: run an experiment, then plan using its saved ID."""

import os

import streamlit as st
from live_ui import render_live
from planning_ui import render_planning

from llm_cost_router.core.setup_logger import setup_logger

setup_logger()

API_URL = os.getenv("LLM_ROUTER_API_URL", "http://127.0.0.1:8000").rstrip("/")

st.set_page_config(page_title="LLM Cost Router", page_icon="💸", layout="wide")
st.caption("SUNDAY AI BUILDS / 01")
st.title("Your AI architecture is also a financial architecture.")
mode = st.radio("Mode", ["Live OpenAI experiment", "Cost planning"], horizontal=True, key="view")
if mode == "Live OpenAI experiment":
    render_live(API_URL)
else:
    render_planning(API_URL)
