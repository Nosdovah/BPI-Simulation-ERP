#!/usr/bin/env bash
# Startup untuk PaaS (Railway / Render): seed kalau DB belum ada, lalu jalankan ERP.
set -e
cd "$(dirname "$0")"
if [ ! -f db/wms.db ]; then
  echo "[osr-erp] seeding database (first boot)…"
  python db/seed.py
fi
echo "[osr-erp] starting uvicorn on :${PORT:-8000} (OSR_MODE=${OSR_MODE:-baseline})"
exec uvicorn apps.wms-stub.main:app --host 0.0.0.0 --port "${PORT:-8000}"