# PostgreSQL on Windows

Docker Compose is now the default. Follow [Docker operations](docker.md) and the root README.

The earlier installation under `var/postgres` remains until data has been backed up, restored and verified in Docker.
Use `scripts/docker.ps1 MigrateNative`, then `scripts/docker.ps1 RemoveNative`.
The old `scripts/postgres.py` helper is retained for pre-removal recovery only; do not run setup for new installations.

