# -*- coding: utf-8 -*-
"""compat.py — kontrak baca untuk webstore, di atas engine ERP/WMS blueprint v2.

Webstore (`Nosdovah/BPI-Simulation-Webstore`) menarik master data lewat
`lib/erp/pull.mjs` dengan lima endpoint:

    GET /api/v2/master/products?limit=5000
    GET /api/v2/inventory/balances?limit=5000
    GET /api/v2/assembly/boms
    GET /api/v2/master/warehouses
    GET /api/v2/master/operational

Modul ini menyajikan bentuk JSON tersebut dari SQLite ERP ini (`db/wms.db`),
bukan dari MOAI SCM-ERP. Engine v2 (`/api/v1/*`) tetap yang mengeksekusi
BOM/kitting, checkout, dan transisi status; router ini murni *read model*
untuk storefront.

Tidak ada angka yang dikarang:
  * `boms` kosong saat OSR_MODE=baseline karena `sku_bom` memang 0 baris.
  * `operational` kosong karena ERP belum menyimpan parameter itu.
"""
import os
import sqlite3

from fastapi import APIRouter, Header, HTTPException, Query

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WMS_DB = os.path.join(BASE, "db", "wms.db")

# Kalau diisi, seluruh endpoint /api/v2/* mewajibkan Bearer token yang sama.
SERVICE_TOKEN = os.environ.get("ERP_SERVICE_TOKEN", os.environ.get("OSR_ERP_SERVICE_TOKEN", ""))

router = APIRouter(prefix="/api/v2", tags=["storefront-compat"])


def _q(sql, args=()):
    conn = sqlite3.connect(WMS_DB)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def _auth(authorization: str | None):
    if not SERVICE_TOKEN:
        return
    if (authorization or "") != f"Bearer {SERVICE_TOKEN}":
        raise HTTPException(401, "Token service ERP tidak valid")


@router.get("/master/products")
def master_products(limit: int = Query(5000, ge=1, le=20000),
                    authorization: str | None = Header(default=None)):
    """Master SKU. `brand` = segmen gender (dipakai filter UI), `item_type` = SINGLE/BUNDLE."""
    _auth(authorization)
    rows = _q(
        "SELECT v.sku, v.name, v.gender, v.price_idr, v.is_bundle, "
        "       COALESCE(SUM(s.available), 0) AS stock, "
        "       COALESCE(SUM(s.reserved), 0)  AS reserved "
        "FROM variants v LEFT JOIN stocks s ON s.variant_sku = v.sku "
        "WHERE v.is_active = 1 GROUP BY v.sku ORDER BY v.sku LIMIT ?",
        (limit,),
    )
    products = [{
        "id": r["sku"],
        "sku": r["sku"],
        "name": r["name"],
        "brand": r["gender"],
        "category": "BUNDLING" if r["is_bundle"] else "EAU DE PARFUM",
        "item_type": "BUNDLE" if r["is_bundle"] else "SINGLE",
        "specs": f"Perpack WMS v2 · {r['gender']} · {'paket rakitan' if r['is_bundle'] else 'satuan'}",
        "stock": r["stock"],
        "bin_code": None,
        "uom": "PCS",
        "cost_price": None,
        "unit_price": r["price_idr"],
        "retail_usd": None,
        "wholesale_price": None,
        "warranty": None,
        "is_assembly": bool(r["is_bundle"]),
        "is_serial": False,
    } for r in rows]
    return {"count": len(products), "products": products}


@router.get("/inventory/balances")
def inventory_balances(limit: int = Query(5000, ge=1, le=50000),
                       authorization: str | None = Header(default=None)):
    _auth(authorization)
    rows = _q(
        "SELECT variant_sku AS sku, "
        "       COALESCE(SUM(available), 0) AS available, "
        "       COALESCE(SUM(reserved), 0)  AS allocated, "
        "       COALESCE(SUM(on_order), 0)  AS on_order "
        "FROM stocks GROUP BY variant_sku ORDER BY variant_sku LIMIT ?",
        (limit,),
    )
    products = [{
        "sku": r["sku"],
        "stock_on_hand": r["available"] + r["allocated"],
        "allocated": r["allocated"],
        "available": r["available"],
        "on_order": r["on_order"],
        "bin_code": None,
        "valuation": None,
    } for r in rows]
    return {"count": len(products), "products": products}


@router.get("/inventory/by-warehouse")
def inventory_by_warehouse(authorization: str | None = Header(default=None)):
    """Stok per gudang (7.325 baris) — dipakai untuk bukti multi-gudang."""
    _auth(authorization)
    rows = _q(
        "SELECT s.variant_sku AS sku, s.warehouse_id, w.name AS warehouse_name, "
        "       s.available, s.reserved, s.on_order "
        "FROM stocks s JOIN warehouses w ON w.id = s.warehouse_id "
        "ORDER BY s.variant_sku, s.warehouse_id"
    )
    return {"count": len(rows), "stocks": rows}


@router.get("/assembly/boms")
def assembly_boms(authorization: str | None = Header(default=None)):
    """BOM rakitan dari `sku_bom`.

    BASELINE: 0 baris -> daftar kosong. Webstore menurunkan produk `kind=BUNDLE`
    dari daftar ini, jadi kosong = komposisi paket tidak bisa dihitung.
    """
    _auth(authorization)
    rows = _q(
        "SELECT b.bundle_sku, b.component_sku, b.multiplier_qty, b.source, b.synced_at, "
        "       v.name AS bundle_name, c.name AS component_name, c.price_idr AS component_price "
        "FROM sku_bom b "
        "LEFT JOIN variants v ON v.sku = b.bundle_sku "
        "LEFT JOIN variants c ON c.sku = b.component_sku "
        "ORDER BY b.bundle_sku, b.component_sku"
    )
    grouped: dict[str, dict] = {}
    for r in rows:
        b = grouped.setdefault(r["bundle_sku"], {
            "target_sku": r["bundle_sku"],
            "target_product_id": r["bundle_sku"],
            "name": r["bundle_name"],
            "version": "BOM-v2",
            "source": r["source"],
            "synced_at": r["synced_at"],
            "labor_cost": 0,
            "overhead_cost": 0,
            "components": [],
        })
        b["components"].append({
            "component_sku": r["component_sku"],
            "component_name": r["component_name"],
            "qty": r["multiplier_qty"],
            "unit_price": r["component_price"],
            "scrap_pct": 0,
        })
    return {"count": len(grouped), "boms": list(grouped.values())}


@router.get("/assembly/bundle-stock")
def bundle_stock(authorization: str | None = Header(default=None)):
    """Kuota statis vs stok terhitung dari komponen — sumber angka phantom stock."""
    _auth(authorization)
    quotas = _q("SELECT bundle_sku, qty, set_by, set_at FROM bundle_quota_manual")
    out = []
    for qu in quotas:
        comps = _q(
            "SELECT b.component_sku, b.multiplier_qty, COALESCE(SUM(s.available), 0) AS available "
            "FROM sku_bom b LEFT JOIN stocks s ON s.variant_sku = b.component_sku "
            "WHERE b.bundle_sku = ? GROUP BY b.component_sku",
            (qu["bundle_sku"],),
        )
        derived = (min(c["available"] // max(1, c["multiplier_qty"]) for c in comps)
                   if comps else None)
        out.append({
            "bundle_sku": qu["bundle_sku"],
            "quota_static": qu["qty"],
            "components": comps,
            "bom_available": bool(comps),
            "stock_from_components": derived,
            "phantom": (qu["qty"] - derived) if derived is not None else None,
            "set_by": qu["set_by"],
        })
    return {"count": len(out), "bundles": out}


@router.get("/master/warehouses")
def master_warehouses(authorization: str | None = Header(default=None)):
    _auth(authorization)
    rows = _q("SELECT id, name FROM warehouses ORDER BY id")
    warehouses = [{
        "id": str(r["id"]),
        "code": f"WH-{r['id']:02d}",
        "name": r["name"],
        "type": "DC",
    } for r in rows]
    return {"count": len(warehouses), "warehouses": warehouses}


@router.get("/master/operational")
def master_operational(authorization: str | None = Header(default=None)):
    """Belum ada tabel parameter operasional di ERP ini -> kosong, bukan dikarang."""
    _auth(authorization)
    return {"count": 0, "operational": []}