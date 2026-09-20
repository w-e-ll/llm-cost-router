#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=app_password="$POSTGRES_APP_PASSWORD" <<'SQL'
CREATE ROLE llm_router LOGIN PASSWORD :'app_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE llm_cost_router OWNER llm_router;
CREATE DATABASE llm_cost_router_test OWNER llm_router;
SQL
