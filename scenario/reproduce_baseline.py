# -*- coding: utf-8 -*-
"""Reproduksi cacat baseline ERP — Jalur A (phantom stock) + Jalur B (order macet).

Membaca `db/wms.db` (skema blueprint v2) dan mencetak angka baseline vs target paper.
Jalankan setelah `python db/seed.py`:

    python scenario/reproduce_baseline.py
"""
import os
import sqlite3

from scipy import stats

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WMS_DB = os.path.join(BASE, "db", "wms.db")
FOCAL_BUNDLE = "Sanctum+Noctis-20ml-A1"     # SKU bundle fokus (§5.1 blueprint v2)


def sigma_of(dpmo):
    """Sigma level dari DPMO (pergeseran 1,5 sigma)."""
    yield_ = 1 - dpmo / 1_000_000
    return stats.norm.ppf(yield_) + 1.5


def main():
    if not os.path.exists(WMS_DB):
        raise SystemExit("db/wms.db belum ada — jalankan `python db/seed.py` dulu")

    conn = sqlite3.connect(WMS_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    def one(sql, args=()):
        row = cur.execute(sql, args).fetchone()
        return row[0] if row else None

    print("=" * 62)
    print("REPRODUKSI CACAT BASELINE — ERP WMS Perpack (Jalur A + B)")
    print("=" * 62)

    # ---- Jalur A: BOM vs kuota statis ----
    n_bom = one("SELECT COUNT(*) FROM sku_bom")
    is_bundle = one("SELECT is_bundle FROM variants WHERE sku=?", (FOCAL_BUNDLE,))
    quota = one("SELECT qty FROM bundle_quota_manual WHERE bundle_sku=?", (FOCAL_BUNDLE,))
    m2291 = one("SELECT COALESCE(SUM(available),0) FROM stocks WHERE variant_sku='M2291'")
    m2292 = one("SELECT COALESCE(SUM(available),0) FROM stocks WHERE variant_sku='M2292'")

    print("[JALUR A — Katalog & Inventaris]")
    print(f"  sku_bom {n_bom} baris (target: 0 = cacat) | is_bundle={bool(is_bundle)}")
    if quota is None:
        print("  kuota statis: - (mode to-be, kuota manual dilepas)")
    else:
        print(f"  kuota statis {quota:,} vs M2291 {m2291:,} / M2292 {m2292:,}"
              f" -> phantom {m2291 - m2292:,}")

    # ---- Jalur B: order lifecycle + telemetri webhook ----
    n_total = one("SELECT COUNT(*) FROM orders")
    n_stuck = one("SELECT COUNT(*) FROM orders WHERE status='sent'")
    n_gt2 = one("SELECT COUNT(*) FROM orders WHERE status='sent' "
                "AND sent_at < datetime('now','-2 days')")
    n_comp = one("SELECT COUNT(*) FROM orders WHERE status='completed'")
    n_manual = one("SELECT COUNT(*) FROM orders WHERE status='completed' "
                   "AND actor_last='STAFF:manual'")
    n_ev = one("SELECT COUNT(*) FROM wms_webhook_events WHERE lane='4.2'")
    n_valid = one("SELECT COUNT(*) FROM wms_webhook_events WHERE lane='4.2' AND payload_valid=1")
    n_reject = one("SELECT COUNT(*) FROM wms_webhook_events WHERE lane='4.2' AND mapper_rejected=1")

    dpmo = (n_ev - n_valid) / n_ev * 1_000_000 if n_ev else 0

    print("[JALUR B — Order & Status]")
    print(f"  order total {n_total} | macet 'sent' {n_stuck} "
          f"({n_stuck / n_total * 100:.1f}%) | >2 hari {n_gt2} ({n_gt2 / n_total * 100:.1f}%)")
    print(f"  completed {n_comp} | manual {n_manual} "
          f"(manual_rate {n_manual / n_comp * 100 if n_comp else 0:.0f}%)")
    print(f"  webhook {n_ev} event | valid {n_valid} "
          f"({n_valid / n_ev * 100 if n_ev else 0:.1f}%) | reject enum {n_reject}")
    print(f"  DPMO {dpmo:,.0f} -> sigma {sigma_of(dpmo):.2f}" if dpmo < 1_000_000 else
          f"  DPMO {dpmo:,.0f} -> sigma n/a")
    print("-" * 62)
    print("Target paper: stuck 59,0% | >2hri 15,1% | manual 100% | valid 19,4%")
    print("              DPMO 805.825 | sigma 0,64 (to-be 1.000 -> 4,59)")
    print("=" * 62)

    conn.close()


if __name__ == "__main__":
    main()