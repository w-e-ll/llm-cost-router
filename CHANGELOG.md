# Changelog

## [0.2.0] - 2026-09-18

### Added
- Docker runtime/test targets, Compose services for FastAPI, Streamlit, PostgreSQL and Alembic.
- Persistent volumes, health checks, non-superuser app role and separate test database.
- Windows setup, backup/restore verification and gated native PostgreSQL removal.
- Text/JSON logging, rotating files, request/run correlation and business events.
- Architecture decisions, file descriptions and operational guides.

### Changed
- Docker is the default deployment. Native files remain until verified migration/removal.
- README follows the supplied project example with application-specific content.

## [0.1.0] - 2026-09-17

### Added
- Initial FastAPI/Streamlit calculator and synthetic routing comparison.
- Live OpenAI experiments, budget reservation, evaluation and saved run IDs.
- Cost planning based on completed experiments.

PostgreSQL persistence and native Windows setup were added during development on 2026-09-18 before the Docker transition.

