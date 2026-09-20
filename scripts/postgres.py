"""Native project-local PostgreSQL for Windows. No service, Docker, or admin install."""

import argparse
import json
import os
import secrets
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx
import psycopg
from dotenv import dotenv_values
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "var" / "postgres"
BIN = BASE / "runtime" / "pgsql" / "bin"
DATA = BASE / "data"
PORT = 5433
# Windows x86-64 binary link published by EDB for PostgreSQL 18.6.
DOWNLOAD = "https://sbp.enterprisedb.com/getfile.jsp?fileid=1260488"


def run_tool(name, *args, check=True):
    result = subprocess.run(
        [str(BIN / f"{name}.exe"), *map(str, args)],
        capture_output=True,
        text=True,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.stdout:
        print(result.stdout, end="", flush=True)
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr, flush=True)
    if check:
        result.check_returncode()
    return result


def download():
    if (BIN / "postgres.exe").exists():
        return
    BASE.mkdir(parents=True, exist_ok=True)
    archive = BASE / "postgresql.zip"
    if not archive.exists():
        partial = archive.with_suffix(".partial")
        print("Downloading official EDB PostgreSQL Windows binaries...", flush=True)
        with httpx.stream("GET", DOWNLOAD, follow_redirects=True, timeout=60) as response:
            response.raise_for_status()
            if not str(response.url).startswith("https://get.enterprisedb.com/postgresql/"):
                raise RuntimeError("Unexpected download origin")
            if "windows-x64-binaries.zip" not in str(response.url):
                raise RuntimeError("Unexpected download platform")
            with partial.open("wb") as output:
                for chunk in response.iter_bytes(1024 * 1024):
                    output.write(chunk)
        partial.replace(archive)
    destination = (BASE / "runtime").resolve()
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if not item.filename.startswith(("pgsql/bin/", "pgsql/lib/", "pgsql/share/")):
                continue
            target = (destination / item.filename).resolve()
            if not target.is_relative_to(destination):
                raise RuntimeError("Unsafe archive path")
            bundle.extract(item, destination)
    print("PostgreSQL binaries ready.", flush=True)


def start():
    if not (DATA / "PG_VERSION").exists():
        raise RuntimeError("Run setup first")
    status = run_tool("pg_ctl", "-D", DATA, "status", check=False)
    if status.returncode == 0:
        return
    # pg_ctl's restricted-token launcher fails in some managed Windows sessions.
    # Launch the same server directly, hidden, without elevation or a Windows service.
    with (BASE / "server.log").open("ab") as log:
        process = subprocess.Popen(
            [str(BIN / "postgres.exe"), "-D", str(DATA)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError("PostgreSQL exited during startup. Check server.log.")
        ready = subprocess.run(
            [str(BIN / "pg_isready.exe"), "-h", "127.0.0.1", "-p", str(PORT)],
            capture_output=True,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if ready.returncode == 0:
            print(f"PostgreSQL ready on 127.0.0.1:{PORT}.")
            return
        time.sleep(0.2)
    raise RuntimeError("PostgreSQL is still starting; inspect server.log before starting again.")


def setup():
    download()
    credentials_path = BASE / "bootstrap.json"
    if credentials_path.exists():
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    else:
        credentials = {"admin": secrets.token_urlsafe(32), "app": secrets.token_urlsafe(32)}
        with credentials_path.open("x", encoding="utf-8") as file:
            json.dump(credentials, file)
    if not (DATA / "PG_VERSION").exists():
        password_file = BASE / "initdb-password.txt"
        password_file.write_text(credentials["admin"] + "\n", encoding="utf-8")
        try:
            run_tool(
                "initdb",
                "-D",
                DATA,
                "-U",
                "postgres",
                "--encoding=UTF8",
                "--locale=C",
                "--auth-host=scram-sha-256",
                "--auth-local=scram-sha-256",
                f"--pwfile={password_file}",
            )
        finally:
            password_file.unlink(missing_ok=True)
        with (DATA / "postgresql.conf").open("a", encoding="utf-8") as file:
            file.write(
                f"\nlisten_addresses = '127.0.0.1'\nport = {PORT}\n"
                "max_connections = 40\nshared_buffers = '64MB'\n"
            )
    start()
    with psycopg.connect(
        host="127.0.0.1",
        port=PORT,
        dbname="postgres",
        user="postgres",
        password=credentials["admin"],
        autocommit=True,
    ) as conn:
        if not conn.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", ("llm_router",)
        ).fetchone():
            conn.execute(
                sql.SQL(
                    "CREATE ROLE llm_router LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                ).format(sql.Literal(credentials["app"]))
            )
        for name in ("llm_cost_router", "llm_cost_router_test"):
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                conn.execute(
                    sql.SQL("CREATE DATABASE {} OWNER llm_router").format(sql.Identifier(name))
                )
    env_path = ROOT / ".env"
    existing = dotenv_values(env_path) if env_path.exists() else {}
    with env_path.open("a", encoding="utf-8") as file:
        for key, name in (
            ("DATABASE_URL", "llm_cost_router"),
            ("TEST_DATABASE_URL", "llm_cost_router_test"),
        ):
            if not existing.get(key):
                file.write(
                    f"\n{key}=postgresql+psycopg://llm_router:{credentials['app']}@127.0.0.1:{PORT}/{name}\n"
                )
    print(f"PostgreSQL ready on 127.0.0.1:{PORT}. Credentials saved locally; not printed.")
    print("Next: uv run alembic upgrade head")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["setup", "start", "stop", "status"])
    action = parser.parse_args().action
    if os.name != "nt":
        parser.error("This helper is for native Windows PostgreSQL.")
    if action == "setup":
        setup()
    elif action == "start":
        start()
    elif action == "stop":
        run_tool("pg_ctl", "-D", DATA, "-m", "fast", "-w", "stop")
    else:
        sys.exit(run_tool("pg_ctl", "-D", DATA, "status", check=False).returncode)


if __name__ == "__main__":
    try:
        main()
    except (
        RuntimeError,
        OSError,
        subprocess.SubprocessError,
        httpx.HTTPError,
        psycopg.Error,
    ) as exc:
        print(
            f"PostgreSQL operation failed ({type(exc).__name__}). Check var/postgres/server.log.",
            file=sys.stderr,
        )
        sys.exit(1)
