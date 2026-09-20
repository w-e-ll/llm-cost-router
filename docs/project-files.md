# Project files explained

| File | Purpose | Maintenance |
|---|---|---|
| `.env` | Private keys, generated database passwords, ports and logging | Keep local and ignored |
| `.env.example` | Safe configuration template | Document settings without real secrets |
| `Dockerfile` | Python runtime/test images with frozen uv dependencies | Explicitly copy runtime files; rebuild after changes |
| `docker-compose.yml` | Services, networks, volumes, health checks, startup order | Use db/api service names inside containers |
| `.dockerignore` | Excludes credentials, native data, backups, logs and local environments | Extend for new generated/private paths |
| `.gitattributes` | LF endings for Linux shell scripts | Required for Windows clones |
| `pyproject.toml` | Metadata, dependencies, pytest and Ruff | Update through uv |
| `uv.lock` | Exact dependency resolution | Commit with dependency changes |
| `README.md` | Overview, architecture, setup, demo, tests and limits | Keep aligned with behavior |
| `DECISIONS.md` | Decisions, reasons and tradeoffs | Revise after architectural changes |
| `CHANGELOG.md` | Dated release history | Record meaningful changes |
| `VERSION.txt` | Release version | Match pyproject, API and Compose image |
| `alembic.ini`, `migrations/` | Database schema lifecycle | Add revisions rather than editing deployed history |
| `docker/init-db.sh` | Fresh-volume role and database initialization | Existing volumes are not reinitialized |
| `scripts/docker.ps1` | Windows lifecycle and verified migration | Never delete unverified database files |
| `core/setup_logger.py` | Shared application logging | Operational metadata only |

The example `.Dockerfile` becomes standard **Dockerfile**, which Docker discovers automatically. uv replaces a duplicate requirements.txt. No CodeScope credentials, repository URLs, license claims or demo-video links are copied.

