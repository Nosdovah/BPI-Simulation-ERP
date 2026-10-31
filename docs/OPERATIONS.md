# Administrator & Operations Documentation — ERP Perpack Replica (Octarine)

Repositori: **`BPI-Simulation-ERP`** (public) — sisi **ERP/WMS** dari simulasi BPI-DMAIC integrasi Octarine Webcommerce ↔ WMS Perpack.

Dokumen ini = panduan operasional + rujukan lengkap endpoint + daftar fitur yang **belum** dikembangkan.

---

## 1. Ringkasan

- **Apa ini:** replika **API ERP/WMS Perpack** (`octarine-api.perpack.id/v1`) berbasis FastAPI + SQLite, plus replika UI-nya. Dibangun sebagai **AS-IS → TO-BE** untuk tesis DMAIC, dengan webhook API sebagai jembatan data ERP ↔ storefront.
- **Kontrak kanonik = replika Perpack** (bukan blueprint-v2). Skema/kontrak lama `/api/v2/*` **ditinggalkan**.
- **Fokus replika:** Master Data Produk + Outbound Sales (sesuai arahan). Modul Perpack lain di luar ruang lingkup (lihat §10).
- **Data:** nyata dari API Perpack, PII ter-anonimkan; angka bisnis asli dipertahankan.

### Angka data (seed)

| Entitas | Jumlah |
|---|---|
| Product group | 302 |
| Product variant | 3.727 |
| Bundle (`is_bundle` + `components`) | 3.039 |
| Sales order (channel WEBSTORE) | 459 |
| Warehouse | 2 |
| Channel | 10 (id 13 = WEBSTORE) |

### Live

| Item | Nilai |
|---|---|
| URL | `https://web-production-e60b55.up.railway.app` |
| UI replica (Sales Order + Produk + Webhook) | `…/` |
| Swagger UI | `…/docs` |
| OpenAPI JSON | `…/openapi.json` |
| Health | `…/healthz` |

---

## 2. Arsitektur & arus data

```
                 ┌─────────────────────────────────────────────┐
                 │        ERP Perpack Replica (FastAPI)         │
                 │                                               │
  Storefront ──▶ │  GET  /api/webhooks/catalog  (pull master)   │
  (webstore)     │  POST /api/webhooks/purchase-order (PO → order) │
                 │  POST /api/webhooks/wms  (status / stok mutasi)│
                 │                                               │
                 │  ┌─────────────── SQLite (db/wms.db) ───────┐ │
                 │  │ product_groups · product_variants        │ │
                 │  │ variant_components (BOM) · variant_stocks │ │
                 │  │ orders (40 field) · webhook_events        │ │
                 │  └───────────────────────────────────────────┘ │
                 │                                               │
                 │  GET /api/to-be/bundle-stock  (BOM + phantom) │
                 └───────────────────────────────────────────────┘
```

**Arah data saat ini (implemented):**

1. **Pull katalog** — storefront `GET /api/webhooks/catalog`.
2. **Push order** — storefront `POST /api/webhooks/purchase-order` → order `paid`.
3. **Push mutasi** — storefront `POST /api/webhooks/wms` (`order.status_changed`, `stock.updated`).

**Belum ada:** push outbound ERP → storefront (HMAC) — lihat §10.

---

## 3. Struktur repo

```
BPI-Simulation-ERP/
├── apps/wms-stub/
│   ├── main.py            # ⭐ aplikasi utama FastAPI (13 endpoint)
│   ├── static/index.html  # ⭐ UI replika Perpack (disajikan di /)
│   └── compat.py          # ❌ DEPRECATED — router /api/v2/* (skema lama → 500)
├── db/
│   ├── schema.sql         # skema tabel Perpack + webhook_events
│   └── seed.py            # seeder dari data/perpack_seed.json (+ norm_ts timestamp)
├── data/
│   └── perpack_seed.json  # data nyata Perpack (anonymized)
├── docs/
│   ├── API_INTEGRATION.md # kontrak webhook untuk tim storefront
│   └── OPERATIONS.md      # dokumen ini
├── main.py                # ❌ DEPRECATED — stub blueprint-v2 lama (OSR_MODE/Jalur A-B)
├── scenario/
│   └── reproduce_baseline.py  # ❌ DEPRECATED — baca skema blueprint-v2
├── apps/admin/            # ❌ LEGACY — build Vite osr-admin (digantikan static/index.html)
├── start.sh               # entrypoint deploy (seed → uvicorn)
├── Procfile               # web: bash start.sh
├── requirements.txt       # fastapi · uvicorn · httpx
└── .env.example           # ⚠️ sisa blueprint-v2 (HMAC outbound — belum terpakai)
```

---

## 4. Model data (tabel SQLite `db/wms.db`)

| Tabel | Isi |
|---|---|
| `channels` | channel penjualan (id, name, image, status) |
| `warehouses` | warehouse (id, name, …) |
| `product_groups` | grup produk (id, name, sku, harga min/max, created_at, …) |
| `product_variants` | varian (id, product_group_id, name, sku, sku_code, alias, sell_price, purchase_price, is_bundle, …) |
| `variant_components` | **BOM** bundle (variant_id, product_id, component_sku, multiplier_qty) |
| `variant_stocks` | stok per warehouse (variant_id, warehouse_id, available, on_order, reserved) |
| `variant_images` | gambar varian (variant_id, url, filename) |
| `orders` | sales order — 40 field (reference_no, order_at, deadline_at, qty, amount, channel_name, courier, status, product_summary, awb, …) |
| `webhook_events` | **audit** semua webhook masuk (event_id, event, payload, effect, received_at) |
| `users` | user + role RBAC (username, email, password_hash, role, warehouse_id, status) |
| `auth_tokens` | token sesi login (token_hash, user_id, expires_at) |

**Kunci:** `orders.reference_no` (string unik) = kunci order. `variant_components` = sumber kebenaran BOM (bundle → komponen + multiplier).

**Timestamp:** semua field waktu memakai **RFC3339 UTC `Z`** (`order_at`, `created_at`, `updated_at`, `received_at`). Normalisasi di `seed.py` (`norm_ts`) + penulisan webhook (`strftime('%Y-%m-%dT%H:%M:%SZ','now')`).

---

## 5. Deployment — Administrator

### 5.1 Railway (produksi/demo)

- `Procfile` → `web: bash start.sh`.
- `start.sh`: seed DB saat **boot pertama** (kalau `db/wms.db` belum ada) → `uvicorn apps.wms-stub.main:app`.
- SQLite **ephemeral** → tiap deploy kondisi bersih (auto-seed). Bagus untuk demo; jangan andalkan persistensi data.
- Push ke branch `main` → **auto-deploy**.

### 5.2 Lokal

```bash
pip install -r requirements.txt     # fastapi, uvicorn[standard], httpx

# 1. seed database (membaca data/perpack_seed.json)
python3 db/seed.py

# 2. jalankan API + UI (port 8100)
cd apps/wms-stub && uvicorn main:app --host 0.0.0.0 --port 8100
```

Buka `http://localhost:8100/` (UI) atau `http://localhost:8100/docs` (Swagger).

> Catatan lingkungan dev ini: interpreter yang dipakai `~/.hermes/cache/scratch/simenv/bin/python` (venv sudah berisi fastapi/uvicorn).

### 5.3 Environment variables

| Var | Status | Keterangan |
|---|---|---|
| `PORT` | aktif (Railway) | port listen uvicorn (default 8000) |
| `WEBHOOK_URL` / `WEBHOOK_SECRET` | ❌ tidak dipakai | sisa blueprint-v2 (§10.5), jangan dijadikan rujukan |

---

## 6. Referensi API lengkap — Operations

Semua response memakai **envelope**: `{"code", "msg": {"id","en"}, "data", "metadata"}`.
Auth **opsional** (tanpa token). CORS `*`.

### 6.1 Kesehatan

| Method | Path | Keterangan |
|---|---|---|
| GET | `/healthz` | `{"ok":true,"service":"perpack-wms-replica"}` |

### 6.2 Replika API Perpack (1:1)

| # | Method | Path | Fungsi |
|---|---|---|---|
| 1 | GET | `/v1/channels` | daftar channel (id 13 = WEBSTORE) |
| 2 | GET | `/v1/warehouses` | daftar warehouse (2) |
| 3 | GET | `/v1/products/group/page?page=&length=` | master-data produk: grup + `variants[]` nested (stok per warehouse) |
| 4 | GET | `/v1/products/list?page=&length=` | varian flat |
| 5 | POST | `/v1/orders/page` | sales order (outbound/sales), body `{page,length,channel_id?,status?,search?}` |

**Contoh `POST /v1/orders/page`** → `data[]` order **40 field**: `id, reference_no, order_at, created_at, process_at, returned_at, deadline_at, qty, amount, cogs, shipping_fee, cod_fee, courier, courier_label, warehouse_name, channel_name, channel_image, customer_username, customer_name, customer_phone, customer_email, picklist_id, picklist_no, delivery_note_id, delivery_note_no, awb, is_shipping_label_printed, printed, product_summary, integration_id, integration_store, is_cod, is_pre_order, is_instant, is_booking, is_fake_order, status, status_mp, sent_at, updated_at`.

### 6.3 Webhook jembatan data (untuk storefront)

| # | Method | Path | Fungsi |
|---|---|---|---|
| 6 | GET | `/api/webhooks/catalog?page=&length=` | master-data produk **flat** untuk storefront |
| 7 | POST | `/api/webhooks/purchase-order` | terima PO → buat order (status `paid`) |
| 8 | POST | `/api/webhooks/wms` | mutasi (`stock.updated` · `order.status_changed`) |
| 9 | GET | `/api/webhooks/wms?limit=` | riwayat/audit webhook |

**6.3a — `GET /api/webhooks/catalog`** (respons elemen):
```json
{ "id":4400, "name":"Serenity+Solace-100ml-A2", "sku":"Serenity+Solace-100ml-A2",
  "sell_price":169000, "purchase_price":0, "is_bundle":true,
  "components":[{"product_id":4394,"component_sku":"M2405","multiplier_qty":1},
                {"product_id":4395,"component_sku":"M2406","multiplier_qty":1}],
  "images":["https://…"], "stock_available":8110 }
```
`metadata`: `{page, length, count:3727, total_page}`.

**6.3b — `POST /api/webhooks/purchase-order`** (request):
```json
{ "reference_no":"ORD-QA-001", "channel":"WEBSTORE",
  "customer_name":"QA", "customer_phone":"0812…", "customer_email":"…",
  "items":[{"sku":"M2405","qty":1,"price":98000}] }
```
Effect: `INSERT orders` status `paid`; `product_summary` = ringkasan item.

**6.3c — `POST /api/webhooks/wms`** (request):
```json
{ "event":"order.status_changed", "event_id":"evt-1",
  "data":{"ref":"ORD-QA-001","status":"packing","tracking_no":"AWB-001"} }
```
```json
{ "event":"stock.updated", "event_id":"evt-2",
  "data":{"materialCode":"M2405","delta":2} }
```
Effect: `order.status_changed` → UPDATE status/awb/updated_at; `stock.updated` → kurangi `variant_stocks.available` sebesar `delta` (positif = kurangi). Semua event tersimpan di `webhook_events`.

### 6.4 TO-BE BOM (virtual kitting)

| # | Method | Path | Fungsi |
|---|---|---|---|
| 10 | GET | `/api/to-be/bundle-stock?sku=&limit=` | stok bundle dihitung dari komponen + phantom |
| 11 | GET | `/api/to-be/sales-summary` | KPI Sales Order / Sales Product |

**`GET /api/to-be/bundle-stock`** respons:
```json
{ "summary":{"total_bundles":3039,"oversell":1,"understate":916,"matched":2122},
  "bundles":[{"sku":"Serenity+Solace-100ml-A2","is_bundle":true,
    "stock_as_is":8110,"stock_to_be":8110,"phantom":0,
    "components":[{"component_sku":"M2405","multiplier":1,"stock":…},
                  {"component_sku":"M2406","multiplier":1,"stock":…}]}] }
```
Rumus: `stock_to_be = min(⌊stok_komponen / multiplier⌋)`; `phantom = stock_as_is − stock_to_be`.

### 6.5 Authentication & RBAC (User Role + Access)

| Method | Path | Fungsi | Akses |
|---|---|---|---|
| POST | `/api/auth/register` | daftar user baru (role default `sales`; `admin`/`warehouse` hanya oleh admin) | publik |
| POST | `/api/auth/login` | login → `{token, user}` | publik |
| GET | `/api/auth/me` | profil user dari token | token |
| POST | `/api/auth/logout` | cabut token sesi | token |
| GET | `/api/auth/users` | daftar semua user | `admin` |
| PATCH | `/api/auth/users/{id}/role` | ubah role user | `admin` |

**Role (3):** `admin` (Super Admin) · `sales` (Tim Sales) · `warehouse` (Tim Gudang).

**Hak akses halaman (UI):**

| Halaman | Super Admin | Tim Sales | Tim Gudang |
|---|---|---|---|
| Outbound Sales | ✓ | ✓ | ✗ |
| Master Data Produk | ✓ | ✗ | ✓ |
| Settings (User & Role) | ✓ | ✗ | ✗ |
| Inbound / Inventory / Report | Coming Soon (semua) | Coming Soon | Coming Soon |

**Mutasi data** (`purchase-order`, `wms` POST): default terbuka (`AUTH_ENFORCED=0`). `AUTH_ENFORCED=1` → wajib login (role aktif apa pun); tanpa token → 401.

**Token:** `Authorization: Bearer <token>` (juga diterima `Authorization: <token>`), berlaku 24 jam (`auth_tokens`). Password di-hash **PBKDF2-SHA256** (100.000 iterasi + salt acak).

**Seed default:** username `admin` / password `admin123` (role `admin`).

**Menyalakan penegakan write:** env `AUTH_ENFORCED=1` (default `0` = demo terbuka).

### 6.6 Export Excel

| Method | Path | Fungsi |
|---|---|---|
| GET | `/api/export/orders` | Outbound Sales → `.xlsx` (kolom: reference, tgl, penerima, lokasi, qty, total, sumber, kurir, status) |
| GET | `/api/export/products` | Master Data Produk → `.xlsx` (id, nama, sku, sku_code, harga, bundle) |

### 6.7 UI

| Method | Path | Keterangan |
|---|---|---|
| GET | `/` | SPA dari `static/index.html`: **login → register → dashboard** (Outbound Sales, Master Data, Settings RBAC, Coming Soon Inbound/Inventory/Report, tombol Export Excel). Sidebar difilter sesuai role. Font Nunito, navy `#163A50`. |

---

## 7. Kontrak format data (yang WAJIB diikuti storefront)

- **Envelope** `{code, msg:{id,en}, data, metadata}` di semua endpoint.
- **Field nama asli ERP**: `sell_price` (bukan `price_idr`), `status` (bukan `order_status`), `reference_no`, `amount`. Harga integer Rupiah.
- **SKU**: komponen `M2406-Solace-100ml-A2`; bundle ditandai `is_bundle:true` + `components[]`, nama pakai `+` (`Serenity+Solace-100ml-A2`).
- **Status order enum (target whitelist)**: `paid · process · picking · packing · packed · ready_to_pickup · sent · completed · cancelled · returned`.
- **Timestamp**: RFC3339 UTC `Z`.
- Kalau ada mismatch → yang diubah **storefront**, bukan ERP.

---

## 8. Logika TO-BE BOM (virtual kitting)

AS-IS Perpack menyimpan stok bundle sebagai angka **statis** (akar phantom/oversell). TO-BE menghitung stok bundle dari **komponen aktual**:

```
stock_to_be(bundle) = min_{komponen} floor( available(komponen) / multiplier_qty )
phantom             = stock_as_is − stock_to_be
```

Hasil pada seed: 3.039 bundle → **1 oversell** + **916 understate** (phantom). Endpoint `/api/to-be/bundle-stock` mengekspos angka ini sebagai bukti inti DMAIC.

---

## 9. Auth & CORS

- **User & role:** fitur register + login + RBAC SUDAH ada (§6.5) — tabel `users` + `auth_tokens`, password PBKDF2, token Bearer 24 jam.
- **Demo:** baca/mutasi data tetap **opsional auth** (tanpa token bisa, default `AUTH_ENFORCED=0`) supaya demo langsung tampil. Set `AUTH_ENFORCED=1` untuk mewajibkan token pada endpoint mutasi (`purchase-order`, `wms` POST).
- **CORS:** `allow_origins=["*"]`.
- Perpack produksi asli memakai header `Authorization: <JWT>` **tanpa `Bearer`** + klaim `client_id`/`warehouse_id` — validasi JWT produksi belum direplika (lihat G6 di §10).

---

## 10. ⚠️ Fitur BELUM dikembangkan + endpoint yang harus dikembangkan

| Gap | State sekarang | Endpoint / tindakan yang perlu dikembangkan |
|---|---|---|
| **G1 — Validasi enum status ketat (FM-6)** | ✅ **SUDAH**: `order.status_changed` menolak status di luar whitelist 11 nilai → HTTP 422 + `data.reason="invalid_status"` | (selesai) |
| **G2 — Guard out-of-order event (FM-5)** | ✅ **SUDAH**: cek `timestamp`/`event_timestamp` per `reference_no` → event lebih tua dari `updated_at` terakhir ditolak HTTP 409 `out_of_order`. Plus **state-machine guard**: status mundur (mis. sent→packed) ditolak HTTP 409 `status_regression` | (selesai) |
| **G3 — Idempotency webhook** | ✅ **sebagian**: `event_id` di `webhook_events` di-dedupe (retry `stock.updated` tidak dobel) + `reference_no` di `purchase-order` idempoten. Belum: idempotensi untuk concurrent (bukan cuma sekuensial) | dedupe penuh + kunci transaksi concurrent |
| **G4 — Oversell prevention / reservasi stok saat checkout** | ✅ **SUDAH**: `purchase-order` memotong stok atomik (cek ketersediaan + potong + buat order dalam SATU transaksi; 409 + `data.insufficient` bila stok kurang) | (selesai) |
| **G5 — Outbound webhook push (ERP → storefront) + HMAC** | ✅ **SUDAH**: sender outbound `X-Perpack-Signature` (HMAC-SHA256) + tabel `push_outbox` + `GET /api/push-outbox` audit; aktif bila `ERP_PUSH_WEBHOOK_URL` diset | (selesai) |
| **G6 — Auth produksi (JWT)** | ✅ **sebagian**: register + login + role (RBAC) + token Bearer **SUDAH ada** (§6.5). Yang masih belum = validasi JWT Perpack `Authorization: <JWT>` **tanpa Bearer** + klaim `client_id`/`warehouse_id` + scoping per warehouse | Middleware validasi JWT Perpack + scoping warehouse |
| **G7 — Lifecycle order penuh** | `purchase-order` hanya set subset 40 field; tidak ada transisi picking→packing→packed→sent + dokumen | Endpoint state-machine + generate `picklist` / `delivery_note` / `awb` |
| **G8 — Modul Perpack lain** | replika hanya Master Data Produk + Outbound Sales (sesuai arahan) | Bila scope diperluas: inbound (`purchase`, `receiving`), inventory (`stocks`, `adjustment`, `transfer`), `picking`, `packing`, `delivery-notes`, `return`, `finance reports`, `marketplace` |
| **G9 — Bersihkan artefak deprecated** | ✅ **SUDAH**: root `main.py`, `compat.py`, `scenario/reproduce_baseline.py` dihapus (commit `3fa403c`) | (selesai) |

**Prioritas saran:** G1 dan G2 paling dekat dengan temuan SoW/FMEA (FM-6, FM-5) — implement sekaligus menjadikan webhook *fail-fast* (tolak input buruk, bukan diam-diam menerima). G3 menyusul untuk idempotensi.

---

## 11. Troubleshooting / catatan operasional

- **`/api/v2/*` 404/500** — bukan bug; kontrak lama sengaja ditinggalkan. Pakai endpoint §6.
- **Data hilang tiap deploy** — SQLite ephemeral di Railway; seed ulang otomatis saat boot. Untuk persistensi perlu volume.
- **`db/*.db` tidak di-commit** (`.gitignore`); yang di-commit hanya `schema.sql` + `seed.py` + `data/perpack_seed.json`.
- **Format waktu** harus `Z`; kalau muncul `+07:00`/spasi berarti seed/webhook belum pakai `norm_ts`/`strftime Z`.
- **UI di `/`** = `static/index.html` (berdiri sendiri, fetch data via fetch ke endpoint); bukan `apps/admin` (legacy Vite).