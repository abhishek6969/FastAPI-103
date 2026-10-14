#!/bin/sh
set -e

# =============================================================================
# Production Container Entrypoint
# =============================================================================

# 1. Start OpenSSH server for Azure App Service Web SSH console (port 2222)
# Generate fresh host keys dynamically at runtime if missing (keeps static images secret-free)
if [ ! -f /etc/ssh/ssh_host_rsa_key ]; then
    ssh-keygen -A > /dev/null 2>&1 || true
fi
service ssh start

# 2. Run database migrations only if explicitly enabled (e.g. RUNNING_LOCAL=true)
# In production, CI/CD pipelines run migrations in an independent step before container deploy.
case "$(echo "${RUNNING_LOCAL:-$RUN_MIGRATIONS}" | tr '[:upper:]' '[:lower:]')" in
    true|1|yes)
        if [ -n "$DATABASE_URL" ]; then
            echo "Applying database migrations via Alembic..."
            alembic upgrade head || echo "Warning: Alembic migration failed or DB not reachable yet."
        fi
        ;;
    *)
        echo "Skipping Alembic migrations (set RUNNING_LOCAL=true or RUN_MIGRATIONS=true to enable)."
        ;;
esac

# 3. Start production ASGI server using Gunicorn + Uvicorn workers
# - WEB_CONCURRENCY defaults to 2 workers (tune based on Azure App Service tier/CPU)
# - UvicornWorker enables high-performance async request handling
# - --timeout 120 prevents premature worker termination on large document OCR
echo "Starting Gunicorn production ASGI server..."
exec gunicorn app.main:app \
    --workers "${WEB_CONCURRENCY:-2}" \
    --worker-class uvicorn.workers.UvicornWorker \
    --bind 0.0.0.0:8000 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
