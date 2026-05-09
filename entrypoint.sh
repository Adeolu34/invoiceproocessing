#!/bin/bash
set -e

SERVICE_TYPE="${SERVICE_TYPE:-web}"

echo "Starting service: $SERVICE_TYPE"

case "$SERVICE_TYPE" in
  web)
    echo "Running database migrations..."
    alembic upgrade head
    echo "Starting FastAPI..."
    exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8001}" --workers 2
    ;;
  worker)
    echo "Starting Celery worker..."
    exec celery -A app.tasks.celery_app.celery_app worker \
      --loglevel=info \
      -Q high,default,low \
      --concurrency=4
    ;;
  beat)
    echo "Starting Celery beat scheduler..."
    exec celery -A app.tasks.celery_app.celery_app beat \
      --loglevel=info \
      --scheduler celery.beat:PersistentScheduler
    ;;
  flower)
    echo "Starting Flower monitor..."
    exec celery -A app.tasks.celery_app.celery_app flower \
      --port="${PORT:-5555}" \
      --broker="${REDIS_URL}"
    ;;
  migrate)
    echo "Running migrations only..."
    exec alembic upgrade head
    ;;
  *)
    echo "Unknown SERVICE_TYPE: $SERVICE_TYPE"
    echo "Valid options: web | worker | beat | flower | migrate"
    exit 1
    ;;
esac
