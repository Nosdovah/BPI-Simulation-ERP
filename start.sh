#!/usr/bin/env bash
# Startup untuk PaaS (Railway / Render): seed kalau DB belum ada, lalu jalankan replica.
set -e
cd "$(dirname "$0")"
if [ ! -f db/wms.db ]; then
  echo "[perpack-replica] seeding database (first boot)…"
  python db/seed.py
fi
echo "[perpack-replica] starting uvicorn on :${PORT:-8000}"
exec uvicorn apps.wms-stub.main:app --host 0.0.0.0 --port "${PORT:-8000}"
