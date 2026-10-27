# BPI-Simulation-ERP — System ERP (WMS Perpack tiruan)

Replika sistem **ERP/WMS Perpack** untuk simulasi BPI-DMAIC integrasi dengan
Octarine Webcommerce (proyek *Peningkatan Kinerja Proses Fulfillment Pesanan*).
Repositori ini memegang **sisi ERP** dari blueprint OSR; storefront + core
(middleware) dikerjakan di repo terpisah oleh anggota tim lain.

## Cakupan

| Komponen | Isi |
|---|---|
| `apps/wms-stub` | ERP (FastAPI :8100) — endpoint katalog, stok, order, state machine, **webhook outbound** |
| `apps/admin` | osr-admin (Vite+React :3001) — panel monitoring |
| `db/` | skema SQLite (`schema.sql`) + seeder (`seed.py`) |
| `scenario/` | `reproduce_baseline.py` — bukti cacat baseline |
| `data/` | `base_catalog.json` — data publik octarine.co.id |

Dua jalur data sesuai blueprint v0.2:

- **Jalur A (4.1)** — Katalog & Inventaris: `GET /v1/open/products/*`, `GET /v1/open/stocks`, webhook `catalog.updated`.
- **Jalur B (4.2)** — Order & Status: `POST /v1/open/sales-order`, `list`, `cancel`, `status`, `process` (baseline 404), webhook `order.status_updated`.

## Webhook sebagai jembatan data

ERP mengirim data ke storefront via **webhook outbound** (bukan pull):

```
wms-stub ──POST {CORE_WEBHOOK_URL}/{token}──▶ storefront/core
          catalog.updated (Jalur A) + order.status_updated (Jalur B)
```

Target dikonfigurasi lewat env (`.env`):

```bash
WEBHOOK_TOKEN=osr-local-token-2026
CORE_WEBHOOK_URL=http://localhost:8000/webhook/octarine-wms
OSR_MODE=baseline      # baseline | tobe
```

`OSR_MODE=baseline` menyalakan seluruh cacat SoW; `tobe` menyalakan perbaikan
(BOM engine, auto-ack, HMAC, no-regression, tracking 3PL).

## Menjalankan (tanpa Docker)

```bash
pip install -r requirements.txt

# 1. seed database (3.696 varian, 7.325 stok, 139 order, 103 event webhook)
python db/seed.py

# 2. jalankan ERP (auto-push katalog via webhook saat start)
cd apps/wms-stub && uvicorn main:app --port 8100

# 3. panel admin
cd apps/admin && npm install && npm run dev   # http://localhost:3001
```

## Reproduksi cacat baseline

`python scenario/reproduce_baseline.py` mencetak (cocok persis paper):

| Metrik | Baseline | Target paper |
|---|---|---|
| Phantom stock bundle (Jalur A) | 995 unit | ±995 ✓ |
| Order macet 'sent' | 82 (59,0%) | 82 (59,0%) ✓ |
| Macet >2 hari | 21 (15,1%) | 21 (15,1%) ✓ |
| Completed manual | 100% | 100% ✓ |
| Webhook valid | 20/103 (19,4%) | 20 (19,4%) ✓ |
| DPMO → sigma | 805.825 → 0,64 | 805.825 → 0,64 ✓ |

## Catatan

- `db/*.db` dan `node_modules/` hasil generate — tidak di-commit (lihat `.gitignore`).
- Kontrak payload webhook mengikuti blueprint §6 (referensi acuan `SCOPE OF WORK`).
- Storefront + core (receiver) berada di repo terpisah — ERP ini cukup mengarahkan
  `CORE_WEBHOOK_URL` + `WEBHOOK_TOKEN` ke sana.