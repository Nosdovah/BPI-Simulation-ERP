# API Integrasi — ERP Perpack Replica (Octarine)

Dokumentasi ini untuk **tim storefront** agar bisa menyambung ke replica ERP/WMS
Perpack (Octarine) tanpa perlu membaca kode backend.

Replica ini meniru API nyata `octarine-api.perpack.id/v1` + menyediakan webhook
jembatan data ERP ↔ storefront. Semua data tersimpan di database (SQLite) dan
di-query via SQL; setiap webhook yang masuk **mengubah data di database**.

---

## ⚠️ PENEGASAN KONTRAK RESMI (WAJIB dibaca storefront)

> **Proyek storefront WAJIB mengikuti FORMAT DATA ERP (Perpack).**
> ERP adalah **sumber kebenaran (single source of truth)**. Storefront tidak
> mendefinisikan format datanya sendiri — **field, envelope, dan enum harus
> mengikuti ERP apa adanya**.

**FORMAT DATA ERP yang wajib diikuti (jangan diganti / di-rename):**

| Aspek | Aturan |
|---|---|
| Envelope | `{ "code", "msg": {"id","en"}, "data", "metadata" }` — berlaku di SEMUA endpoint |
| Field order | nama field mengikuti ERP: `reference_no`, `order_at`, `deadline_at`, `customer_name`, `warehouse_name`, `qty`, `amount`, `channel_name`, `courier`, `status`, `product_summary`, `sku`, `sell_price`, `is_bundle`, `components`, `stock_available` |
| Status enum | huruf kecil: `paid · process · picking · packing · packed · ready_to_pickup · sent · completed · cancelled · returned` |
| Kunci order | `reference_no` (string unik), BUKAN `id` numerik |
| Harga | integer Rupiah (tanpa titik), mis. `98000` |

- **Kontrak resmi** = **replika Perpack** → endpoint `/api/webhooks/*` + `/v1/*`.
- **Skema/kontrak blueprint-v2 SUDAH DITINGGALKAN** — `/api/v2/*`, `compat.py`,
  tabel `sku_bom` / `bundle_quota_manual`, dan engine `/api/v1` lama **jangan
  dipakai** (basisnya skema lama yang sudah diganti).
- **AS-IS** = replika sistem Perpack nyata (data + perilaku asli).
  **TO-BE** = simulasi perbaikan yang dibangun di atas kontrak yang sama.
- Kalau ada **mismatch** field/format antara storefront dan ERP → yang diubah
  adalah **storefront**, bukan ERP.

Storefront cukup memakai **4 endpoint inti**:

1. `GET  /api/webhooks/catalog` — tarik master-data produk.
2. `POST /api/webhooks/purchase-order` — kirim order (checkout).
3. `POST /api/webhooks/wms` — update status order / stok.
4. `GET  /v1/orders/page` — baca balik status order.

> Migrasi dari kontrak lama: `/api/v2/master/products` → `/api/webhooks/catalog`;
> `/api/v2/inventory/balances` → hitung dari `stock_available` di catalog;
> `order.status_changed` → tetap `POST /api/webhooks/wms`.

---

## 1. Base URL & Auth

| Lingkungan | URL |
|---|---|
| Produksi (Railway) | `https://web-production-e60b55.up.railway.app` |
| Lokal | `http://localhost:8100` |

- **Auth: OPSIONAL.** Perpack asli wajib kirim JWT (`Authorization: <jwt>`),
  tapi replica ini (untuk demo/tugas) melonggarkannya — endpoint tetap bisa
  diakses tanpa token. Bila ingin meniru alur asli, kirim header
  `Authorization: <jwt>` (tanpa `Bearer`).
- **CORS**: terbuka (`*`), jadi fetch lintas-origin dari browser storefront
  langsung jalan, tanpa perlu proxy/backend tambahan.

---

## 2. Envelope respons

Semua endpoint mengembalikan bentuk:

```json
{
  "code": 200,
  "msg": { "id": "Sukses", "en": "Success" },
  "data": { ... },
  "metadata": { "page": 1, "length": 100, "count": 3727, "total_page": 38 }
}
```

- `data` = isi; `metadata` hanya ada di endpoint yang ter-paginasi.
- Error 404: `{"code":40401,"msg":{"id":"Api tidak ditemukan",...},"data":null}`.
- Error 401 (bila auth ketat diaktifkan): `{"code":401,"msg":{"id":"tidak memiliki akses",...},"data":null}`.

---

## 3. Webhook jembatan data (yang dipakai storefront)

### 3.1 Tampilkan master-data produk → `GET /api/webhooks/catalog`

Storefront memanggil ini saat page load untuk menampilkan katalog produk.

```
GET /api/webhooks/catalog?page=1&length=250
```

Contoh respons `data` (array produk/varian):

```json
[
  {
    "id": 4400,
    "product_group_id": 563,
    "name": "M2406-Solace-100ml-A2",
    "sku": "M2406-Solace-100ml-A2",
    "sku_code": "",
    "alias": "M2406",
    "sell_price": 98000,
    "purchase_price": 0,
    "is_bundle": true,
    "components": [ { "product_id": 3258, "component_sku": "M2406", "multiplier_qty": 1 } ],
    "images": [ "https://...jpg" ],
    "stock_available": 10715
  }
]
```

- `is_bundle=true` → produk bundling; `components[]` = isi bundel (SKU × qty).
- `stock_available` = total stok tersedia (semua warehouse dijumlah).

### 3.2 Terima order dari checkout → `POST /api/webhooks/purchase-order`

Storefront mengirim purchase order (PO) saat customer checkout. Replica membuat
order baru berstatus awal `paid` di database.

```
POST /api/webhooks/purchase-order
Content-Type: application/json
```

Request body:

```json
{
  "reference_no": "ORD-WEBSTORE-PROD-9001",
  "channel": "WEBSTORE",
  "customer_name": "Budi Santoso",
  "customer_phone": "628123456789",
  "customer_email": "budi@contoh.id",
  "items": [
    { "sku": "M2406-Solace-100ml-A2", "qty": 2, "price": 98000 },
    { "sku": "Serenity+Solace-100ml-A2", "qty": 1, "price": 169000 }
  ]
}
```

- `reference_no` harus **unik** (kalau sudah ada, dijawab `applied:false` + `order_id` lama).
- `items[].price` opsional — kalau kosong, `amount` dijumlah dari `qty × sell_price` di DB.

Respons:

```json
{
  "code": 200,
  "msg": { "id": "Sukses", "en": "Success" },
  "data": {
    "applied": true,
    "order_id": 5826582,
    "reference_no": "ORD-WEBSTORE-PROD-9001",
    "status": "paid",
    "qty": 3,
    "amount": 365000,
    "product_summary": "2 M2406-Solace-100ml-A2, 1 Serenity+Solace-100ml-A2"
  }
}
```

### 3.3 Update status order / stok → `POST /api/webhooks/wms`

Satu endpoint menerima beberapa jenis event dan **langsung mengubah database**.

**a) Ubah status order** (`order.status_changed`):

```json
{
  "event": "order.status_changed",
  "event_id": "evt-1",
  "data": { "ref": "ORD-WEBSTORE-PROD-9001", "status": "process", "tracking_no": "SCP000123" }
}
```

- `ref` = `reference_no` order. `status` enum (huruf kecil):
  `paid · process · picking · packing · packed · ready_to_pickup · sent · completed · cancelled · returned`
- `tracking_no` opsional → mengisi `awb` order.

**b) Ubah stok** (`stock.updated`):

```json
{
  "event": "stock.updated",
  "event_id": "evt-2",
  "data": { "materialCode": "M2406", "delta": 500 }
}
```

- `materialCode` = SKU varian (atau `sku` / `component_sku` / `material_code`).
- `delta` = unit **terjual** → stok dikurangi. (alias field: `qty_sold`, `quantity_sold`, `quantity`).

Respons (contoh):

```json
{ "code": 200, "msg": {"id":"Sukses","en":"Success"},
  "data": { "applied": true, "event": "stock.updated", "event_id": "evt-2",
            "effect": { "applied": true, "sku": "M2406", "variant_id": 3258, "sisa_stok_total": 10715 },
            "total_events": 2 } }
```

### 3.4 Baca riwayat webhook → `GET /api/webhooks/wms`

```
GET /api/webhooks/wms?limit=50
```

Mengembalikan semua event webhook yang pernah masuk (tersimpan di DB), berguna
untuk audit/debug.

---

## 4. Replika API Perpack (referensi / fallback)

Endpoint ini meniru `octarine-api.perpack.id/v1` 1:1 (dipakai UI Perpack asli).

| Method | Path | Fungsi |
|---|---|---|
| GET | `/v1/channels` | daftar channel (13 = WEBSTORE) |
| GET | `/v1/warehouses` | daftar warehouse |
| GET | `/v1/products/group/page?page=&length=` | grup produk + varian nested |
| GET | `/v1/products/list?page=&length=` | varian produk (flat) |
| POST | `/v1/orders/page` | daftar sales order (body `{page,length,channel_id?,status?,search?}`) |

Contoh order (`POST /v1/orders/page`):

```json
{ "page": 1, "length": 10, "channel_id": 13 }
```

Field order lengkap (dipakai juga saat storefront membaca balik status order):

```
id, reference_no, order_at, created_at, process_at, returned_at, deadline_at,
qty, amount, cogs, shipping_fee, cod_fee, courier, courier_label, warehouse_name,
channel_name, channel_image, customer_username, customer_name, customer_phone,
customer_email, picklist_id, picklist_no, delivery_note_id, delivery_note_no,
awb, is_shipping_label_printed, printed, product_summary, integration_id,
integration_store, is_cod, is_pre_order, is_instant, is_booking, is_fake_order,
status, status_mp, sent_at, updated_at
```

---

## 5. Alur integrasi storefront

```
[Storefront]  GET  /api/webhooks/catalog        -> tampil daftar produk
[Storefront]  POST /api/webhooks/purchase-order -> customer checkout, order masuk DB (paid)
[Storefront]  POST /api/webhooks/wms (order.status_changed) -> status maju (process->packing->…->completed)
[Storefront]  GET  /v1/orders/page              -> baca balik status order (opsional)
```

---

## 6. Contoh kode frontend (React / Next.js)

```js
const BASE = "https://web-production-e60b55.up.railway.app";

// 1) Load produk
const loadProducts = async () => {
  const j = await (await fetch(`${BASE}/api/webhooks/catalog?page=1&length=100`)).json();
  return j.data; // [{sku, name, sell_price, is_bundle, components, stock_available, images}]
};

// 2) Checkout -> buat order
const createOrder = async (cart, customer) => {
  const res = await fetch(`${BASE}/api/webhooks/purchase-order`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      reference_no: `ORD-${Date.now()}`,
      channel: "WEBSTORE",
      customer_name: customer.name,
      customer_phone: customer.phone,
      customer_email: customer.email,
      items: cart.map(c => ({ sku: c.sku, qty: c.qty, price: c.sell_price }))
    })
  });
  return res.json(); // -> data.order_id, data.status
};

// 3) Update status order
const updateStatus = (ref, status) =>
  fetch(`${BASE}/api/webhooks/wms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event: "order.status_changed",
      data: { ref, status }
    })
  });
```

---

## 7. Curl cheat sheet

```bash
BASE="https://web-production-e60b55.up.railway.app"

# Produk
curl "$BASE/api/webhooks/catalog?page=1&length=3"

# Buat order
curl -X POST "$BASE/api/webhooks/purchase-order" -H "Content-Type: application/json" \
  -d '{"reference_no":"ORD-TEST-1","customer_name":"Uji","items":[{"sku":"M2406-Solace-100ml-A2","qty":1,"price":98000}]}'

# Ubah status order
curl -X POST "$BASE/api/webhooks/wms" -H "Content-Type: application/json" \
  -d '{"event":"order.status_changed","data":{"ref":"ORD-TEST-1","status":"process"}}'

# Ubah stok
curl -X POST "$BASE/api/webhooks/wms" -H "Content-Type: application/json" \
  -d '{"event":"stock.updated","data":{"materialCode":"M2406","delta":2}}'

# Baca balik order (replika Perpack)
curl -X POST "$BASE/v1/orders/page" -H "Content-Type: application/json" \
  -d '{"page":1,"length":10,"channel_id":13}'
```