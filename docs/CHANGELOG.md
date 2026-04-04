# Changelog - WebGIS AI Agent

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-04-04

### Added
- **T005 Dialog Interface**: New AI chat interface (`POST /api/v1/chat/`)
  - Supports session-based conversations with context preservation
  - Provides GIS-specific AI responses (buffer, clip, intersect, statistics)
  - Includes session management endpoints (list, get, delete, clear)

- **B002 Agent Orchestration**: Agent coordination framework
  - Dynamic tool routing and execution
  - State management and task queuing

- **B003 Data Fetching**: Multi-source data retrieval
  - Vector/raster tile fetching from various sources
  - Geocoding and place search functionality

- **B004 Spatial Analysis Engine MVP**
  - Buffer analysis
  - Clip/Crop analysis  
  - Intersection overlay
  - Union merge operations

- **M002 Issue Tracking System**
  - GitHub webhook integration for issue events
  - Automatic issue state synchronization
  - Timeout detection and escalation workflow
  - Statistics reporting with Feishu notifications
  - Weekly digest report generation

### Fixed
- Various code review fixes patches (#33-36)
- Security enhancements: Password policy enforcement, DEBUG default OFF, Real health checks
- Database credential hardcoding replaced with environment variables
- JWT authentication middleware properly integrated into route layer
- CORS configuration with allow_credentials compatibility fix
- SQLAlchemy base import conflict resolution

### Infrastructure
- Docker containerization with multi-stage builds
- PostgreSQL database with Alembic migrations
- Redis-backed Celery task queue
- Comprehensive test suite (unit + integration tests)

### Dependencies
- FastAPI + Pydantic for REST API
- SQLAlchemy ORM with PostgreSQL
- Celery for async task processing
- JWT for authentication

---

**Note**: This marks the initial production-ready release (v0.1.0).

Previous releases: None (this is the first release)