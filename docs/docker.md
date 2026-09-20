# Docker operations

## Startup
Run `scripts/docker.ps1 Setup`, edit `.env`, then `scripts/docker.ps1 Up`.
For an existing native database, use **MigrateNative before Up**.
The containers include Python/dependencies; OpenAI remains external.
Loopback host ports: API 8000, UI 8501, PostgreSQL 5434.
Inside Docker, UI uses `http://api:8000` and the database uses `db:5432`.

The PostgreSQL 18 image uses versioned data beneath `/var/lib/postgresql`, so the volume mounts that parent. API and UI logs bind-mount directly to `var/logs` for Windows access.
A fresh-volume init script creates the non-superuser app role and app/test databases.
Alembic completes before API startup; UI waits for API health.

## Native migration
Stop local API/UI and close DB clients:

```powershell
.\scripts\docker.ps1 MigrateNative
.\scripts\docker.ps1 RemoveNative
```

The script checks for connected clients and unfinished experiments, dumps to a custom archive,
compares source data before/after backup, restores transactionally into an empty target,
compares complete records, stops native PostgreSQL and checks container restart persistence.
Only a checksum-protected verified marker permits removal of `var/postgres`.
The archive stays in `var/backups/native-migration`.

On any failure, keep the archive and native files. A nonempty target is never automatically cleared.
If restoration succeeded but image startup failed, fix the error and run
`scripts/docker.ps1 CompleteMigration`. It resumes from the checksum-protected restore marker
without trying to overwrite the database. Do not run new experiments until removal is complete.
The old `uv run python scripts/postgres.py start` helper remains available for recovery until native removal.
Do not create new paid runs in both databases during migration.

## Operations

```powershell
docker compose ps
docker compose logs --tail 100 api migrate db
docker compose up -d --build --wait
docker compose down
.\scripts\docker.ps1 Test
```

`down` retains the database volume and host log files. **`down -v` destroys the database volume.**
Restart does not apply changed environment values; run Up.
Image rebuilds do not rotate existing DB passwords.

## Docker database backups

```powershell
New-Item -ItemType Directory -Force var/backups
docker compose exec -T db pg_dump -U llm_router -d llm_cost_router -Fc -f /tmp/router.dump
docker compose cp db:/tmp/router.dump ./var/backups/router.dump
```

Copy backups off-machine and test restoration into a separate empty database.
Use `pg_restore --exit-on-error --single-transaction --no-owner --no-acl`.
Do not overwrite a populated database without a deliberate recovery plan.

## Troubleshooting
- **`docker-credential-desktop` not found:** Docker Desktop's helper is installed under `C:\Program Files\Docker\Docker\resources\bin`, but that directory is missing from the current PATH. `scripts/docker.ps1` now adds it for its own process. Restart PyCharm after a Docker Desktop upgrade if the plain `docker` command still cannot find it.
- **Port already allocated:** stop PyCharm API/UI or change ports in .env.
- **Docker permission denied from Codex:** run the prepared commands in your normal terminal; this session cannot access the engine.
- **Authentication failed after editing .env:** init does not rerun on existing volumes. Restore the prior password or explicitly rotate the role password.
- **Migration failed:** inspect `docker compose logs migrate`; API startup remains blocked.
- **Readiness 503:** inspect migrations and `docker compose logs db api`.
- **Interrupted native migration:** keep both copies; fix the reported error before removal.

## References
- [Compose startup order](https://docs.docker.com/compose/how-tos/startup-order/)
- [Official PostgreSQL image](https://hub.docker.com/_/postgres)
- [uv in Docker](https://docs.astral.sh/uv/guides/integration/docker/)
