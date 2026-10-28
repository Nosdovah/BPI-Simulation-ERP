# BPI-Simulation-ERP — Replika ERP/WMS Perpack (Octarine)

Replika **API ERP/WMS Perpack** (`octarine-api.perpack.id/v1`) untuk simulasi
BPI-DMAIC integrasi Octarine Webcommerce ↔ WMS. Repo ini memegang **sisi ERP**;
storefront + core dikerjakan di repo terpisah oleh tim lain.

> 📘 **Tim storefront — baca dulu: [`docs/API_INTEGRATION.md`](docs/API_INTEGRATION.md)**
> (semua endpoint webhook + payload + contoh kode frontend + curl).

## Live

- **URL**: `https://web-production-e60b55.up.railway.app`
- **UI replica**: buka URL di atas → tabel Sales Order + Produk + Webhook.
- **Swagger**: `https://web-production-e60b55.up.railway.app/docs`

## Data (SQLite, tersimpan & di-query via SQL)

| Entitas | Jumlah |
|---|---|
| Product group | 302 |
| Product variant | 3.727 |
| Bundle (`is_bundle` + `components`) | 3.039 |
| Sales order (channel WEBSTORE) | 459 |
| Warehouse | 2 (Bogor, Offline) |
| Channel | 10 (13 = WEBSTORE) |

Data nyata diambil dari API Perpack (PII pelanggan ter-anonimkan). Setiap
perubahan lewat webhook **dismpan permanen di database** (tabel `orders`,
`product_variants`, `variant_stocks`, `webhook_events`).

## Endpoint

**Webhook jembatan data (untuk storefront):**

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/webhooks/catalog` | master-data produk untuk storefront |
| POST | `/api/webhooks/purchase-order` | terima PO → buat order (status `paid`) |
| POST | `/api/webhooks/wms` | `stock.updated` (ubah stok) · `order.status_changed` (ubah status) |
| GET | `/api/webhooks/wms` | riwayat webhook (audit, dari DB) |

**Replika API Perpack (1:1 dengan aslinya):**

| Method | Path | Fungsi |
|---|---|---|
| GET | `/v1/channels` | daftar channel |
| GET | `/v1/warehouses` | daftar warehouse |
| GET | `/v1/products/group/page` | master-data/product (grup + varian nested) |
| GET | `/v1/products/list` | varian produk (flat) |
| POST | `/v1/orders/page` | outbound/sales (page/length/channel_id) |

Detail payload + contoh: lihat [`docs/API_INTEGRATION.md`](docs/API_INTEGRATION.md).

## Menjalankan lokal

```bash
pip install -r requirements.txt

# 1. seed database (membaca data/perpack_seed.json)
python db/seed.py

# 2. jalankan replica (API + UI pada :8100)
cd apps/wms-stub && uvicorn main:app --port 8100
```

Buka `http://localhost:8100/` untuk UI replica, atau `http://localhost:8100/docs`.

## Deploy

- **Railway** (FastAPI persisten): `Procfile` → `web: bash start.sh` (auto-seed
  saat boot pertama, SQLite ephemeral → tiap deploy = kondisi bersih, bagus
  untuk demo).
- Push ke `main` → auto-deploy.

## Catatan

- `db/*.db` dan `node_modules/` tidak di-commit (lihat `.gitignore`).
- Auth replika di **longgarkan** (opsional) supaya demo mudah; kolom token di UI
  tetap ada bila ingin uji alur autentikasi JWT (Perpack asli: `Authorization: <jwt>` tanpa `Bearer`).