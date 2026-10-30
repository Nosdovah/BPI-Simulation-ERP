# L3 — Real-time Push ERP → Webstore (Spesifikasi TO-BE / Rekomendasi Paper)

**Status:** SPEK rekomendasi TO-BE — **BELUM diimplementasi**. Merujuk gap **G5** di `docs/OPERATIONS.md` §10.

---

## Masalah

ERP saat ini **pull-only** untuk webstore: webstore membaca katalog (`GET /api/webhooks/catalog`) dan memantau status lewat **polling** (`POST /v1/orders/page`). Tidak ada mekanisme push, jadi stok "realtime" hanya bisa lewat cache TTL (L1/L2).

## Tujuan L3

ERP **mengirim webhook outbound** ke webstore saat terjadi perubahan stok/status → webstore update seketika tanpa polling.

---

## Endpoint (dari ERP → ke webstore)

| Item | Nilai |
|---|---|
| Method | `POST` |
| Path (di webstore) | `/api/webhooks/erp/events` |
| URL target | dikonfigurasi ERP via env `ERP_PUSH_WEBHOOK_URL` |
| Secret | env `ERP_PUSH_WEBHOOK_SECRET` (HMAC) |

### Header

| Header | Isi |
|---|---|
| `Content-Type` | `application/json` |
| `X-Perpack-Signature` | `HMAC-SHA256(secret, raw_body)` hex |
| `X-Perpack-Event-Id` | idempotency key (UUID) |
| `X-Perpack-Timestamp` | RFC3339 UTC `Z` |

### Body (envelope)

```json
{ "event":"stock.updated", "event_id":"<uuid>", "timestamp":"2026-10-30T15:00:00Z",
  "data":{"sku":"M2405","warehouse_id":8,"available":10975,"previous":10976} }
```

### Jenis event

| Event | `data` |
|---|---|
| `stock.updated` | `{sku, warehouse_id, available, previous}` |
| `order.status_changed` | `{reference_no, status, previous_status, tracking_no?, updated_at}` |

---

## Sifat yang wajib dijamin

- **Retry:** eksponensial 1s → 2s → 4s → 8s → 16s (maks 5×), lalu drop + log.
- **Idempotensi:** `X-Perpack-Event-Id` unik; receiver (webstore) wajib dedup.
- **Urutan:** **tidak dijamin** — receiver harus pakai `event_id` + `timestamp` untuk reconcile (replay/last-write).
- **Verifikasi:** receiver wajib memvalidasi `X-Perpack-Signature` atas raw body (menolak payload yang tanda tangannya tidak cocok).

---

## Nilai untuk paper (DMAIC)

L3 adalah klaim TO-BE yang terukur: **latensi stok realtime turun dari ~TTL polling (level detik) menjadi < 1 detik**, plus tidak ada lagi beban polling penuh. Ukur `p95` latensi propagasi stok sebelum (L1/L2) vs sesudah (L3) sebagai metrik BAB IV.

## Implementasi (di ERP)

1. Di `apply_stock_update` / `apply_order_status`, setelah mutasi sukses → enqueue payload push.
2. Worker `httpx.AsyncClient` kirim ke `ERP_PUSH_WEBHOOK_URL` dengan header HMAC + retry.
3. Tabel `push_outbox(event_id, event, payload, status, attempts, next_at)` untuk retry + audit.

> **Catatan:** ini perubahan kode ERP di luar "replika Perpack 1:1". Dikerjakan hanya bila pemilik proyek memutuskan L3 masuk scope TO-BE.