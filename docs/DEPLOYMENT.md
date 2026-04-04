# Deployment Guide - v0.1.0

## Prerequisites
- Docker & Docker Compose
- PostgreSQL 14+
- Redis 6+

## Quick Start

### Using Docker Compose (Recommended)
```bash
# Clone and start
git clone <repository>
cd webgis-ai-agent
cp .env.example .env
# Edit .env with your credentials

# Start all services
docker-compose up -d

# View logs
docker-compose logs -f api
```

### Manual Setup
```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export DATABASE_URL="postgresql://user:pass@localhost:5432/webgis"
export REDIS_URL="redis://localhost:6379/0"

# Run migrations
alembic upgrade head

# Start API
python -m uvicorn app.main:app --reload
```

## Environment Variables
| Variable | Description | Required |
|----------|-------------|----------|
| `DATABASE_URL` | PostgreSQL connection string | Yes |
| `REDIS_URL` | Redis connection string | Yes |
| `SECRET_KEY` | JWT signing key | Yes |
| `ENV` | Environment: development/staging/production | Yes |

## API Endpoints
- Health: `GET /api/v1/health`
- Layers: `GET/POST/PUT/DELETE /api/v1/layers/`
- Tasks: `GET/POST /api/v1/tasks/`
- Auth: `/api/v1/auth/*`
- Chat: `POST /api/v1/chat/`

## Testing
```bash
# Run all tests
python -m pytest tests/

# Run specific module
python -m pytest tests/unit/test_chat_api.py -v
```

## Troubleshooting
Check logs: `docker-compose logs api`
Database console: `docker-compose exec db psql -U postgres`
Redis: `docker-compose exec redis redis-cli`