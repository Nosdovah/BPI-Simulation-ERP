# -*- coding: utf-8 -*-
"""Reproduksi cacat baseline ERP — Jalur A (phantom stock) + Jalur B (order macet).

Membaca wms.db dan mencetak angka baseline vs target paper.
"""
import os, sqlite3
from scipy import stats

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WMS_DB = os.path.join(BASE, "db", "wms.db")
FOCAL_BUNDLE = "BND-SANCTUM-NOCTIS-20-A1"


def sigma_of(dpmo):
    yield_ = 1 - dpmo / 1_000_000
    return stats.norm.ppf(yield_) + 1.5


conn = sqlite3.connect(WMS_DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=" * 62)
print("REPRODUKSI CACAT BASELINE — ERP WMS Perpack (Jalur A + B)")
print("=" * 62)

# ---- Jalur A ----
n_bc = cur.execute("SELECT COUNT(*) c FROM bundle_components").fetchone()["c"]
bundle_is = cur.execute("SELECT is_bundle FROM variants WHERE sku=?", (FOCAL_BUNDLE,)).fetchone()["is_bundle"]
quota = cur.execute("SELECT qty FROM bundle_quota_manual WHERE bundle_sku=?", (FOCAL_BUNDLE,)).fetchone()["qty"]
m2291 = cur.execute("SELECT available FROM stocks WHERE variant_sku='M2291'").fetchone()["available"]
m2292 = cur.execute("SELECT available FROM stocks WHERE variant_sku='M2292'").fetchone()["available"]

print("[JALUR A — Katalog & Inventaris]")
print(f"  bundle_components {n_bc} baris (target: 0 = cacat) | is_bundle={bool(bundle_is)}")
print(f"  kuota statis {quota:,} vs M2291 {m2291:,} / M2292 {m2292:,} -> phantom {m2291 - m2292:,}")

# ---- Jalur B ----
n_stuck = cur.execute("SELECT COUNT(*) c FROM orders WHERE status='sent'").fetchone()["c"]
n_gt2 = cur.execute("SELECT COUNT(*) c FROM orders WHERE status='sent' "
                    "AND sent_at < datetime('now','-2 days')").fetchone()["c"]
n_comp = cur.execute("SELECT COUNT(*) c FROM orders WHERE status='completed'").fetchone()["c"]
n_manual = cur.execute("SELECT COUNT(*) c FROM orders WHERE status='completed' AND actor_last='STAFF:manual'").fetchone()["c"]
n_total = cur.execute("SELECT COUNT(*) c FROM orders").fetchone()["c"]
n_ev = cur.execute("SELECT COUNT(*) c FROM wms_webhook_events WHERE lane='4.2'").fetchone()["c"]
n_valid = cur.execute("SELECT COUNT(*) c FROM wms_webhook_events WHERE lane='4.2' AND payload_valid=1").fetchone()["c"]
n_reject = cur.execute("SELECT COUNT(*) c FROM wms_webhook_events WHERE lane='4.2' AND mapper_rejected=1").fetchone()["c"]

defects = n_ev - n_valid
dpmo = defects / n_ev * 1_000_000

print("[JALUR B — Order & Status]")
print(f"  order total {n_total} | macet 'sent' {n_stuck} ({n_stuck/n_total*100:.1f}%) | >2 hari {n_gt2} ({n_gt2/n_total*100:.1f}%)")
print(f"  completed {n_comp} | manual {n_manual} (manual_rate {n_manual/n_comp*100 if n_comp else 0:.0f}%)")
print(f"  webhook {n_ev} event | valid {n_valid} ({n_valid/n_ev*100:.1f}%) | reject enum {n_reject}")
print(f"  DPMO {dpmo:,.0f} -> sigma {sigma_of(dpmo):.2f}")
print("-" * 62)
print("Target paper: stuck 59,0% | >2hri 15,1% | manual 100% | valid 19,4%")
print("              DPMO 805.825 | sigma 0,64 (to-be 1.000 -> 4,59)")
print("=" * 62)