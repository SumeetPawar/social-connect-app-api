# Fitness Tracker API

FastAPI-based fitness tracking application with PostgreSQL.

## Quick Start

1. **Install Python 3.11** (stable version required)

2. **Create virtual environment**
   ```bash
   python -m venv .venv311
   .venv311\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Create `.env` file**
   ```env
   DATABASE_URL=postgresql+asyncpg://fitness:fitnesspass@localhost:5432/fitnessdb
   JWT_SECRET=your-secret-key-here
   ```

5. **Start database**
   ```bash
   docker-compose up -d
   ```

6. **Run migrations**
   ```bash
   alembic upgrade head
   ```

7. **Start server**
   ```bash
   python -m uvicorn app.main:app --reload
   ```

## Access

- API: http://127.0.0.1:8000
- Docs: http://127.0.0.1:8000/docs

## Setup Notes

Fixed during initial setup:
- Upgraded from Python 3.15 alpha to 3.11 (stable)
- Added `psycopg2-binary` for database connectivity
- Added `email-validator` for Pydantic validation
- Updated database URL to use `postgresql+asyncpg://` for async operations
