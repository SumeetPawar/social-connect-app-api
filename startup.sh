#!/bin/bash
echo "Running database migrations..."
python -m alembic upgrade head
echo "Starting application..."
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app --bind=0.0.0.0:8000
