#!/usr/bin/env bash
# Startup untuk PaaS (Railway / Render): seed hanya kalau DB benar-benar kosong/tidak ada, lalu jalankan replica.
# WMS_DB_PATH menunjuk ke volume persistent Railway (default: db/wms.db untuk lokal).
set -e
cd "$(dirname "$0")"
DB_FILE="${WMS_DB_PATH:-db/wms.db}"
if [ ! -s "$DB_FILE" ]; then
  echo "[perpack-replica] DB kosong/tidak ada -> seeding database (first boot)…"
  python db/seed.py
fi
echo "[perpack-replica] starting uvicorn on :${PORT:-8000}"
exec uvicorn apps.wms-stub.main:app --host 0.0.0.0 --port "${PORT:-8000}"
