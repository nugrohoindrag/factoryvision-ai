# shortcut: thin stand-in for the masterdata branch's app.materials. Same signatures as CONTRACT-MR.md; the integrator keeps the real module.
from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from typing import Any

from app import effects, plant, warehouse
from app.errors import FVError
from app.store import store
from app.tools.common import TODAY, to_date

UNKNOWN = "FV-DATA-001"
RESERVATIONS = "material_reservations"
TRANSACTIONS = "material_transactions"

_GROUP = {
    "PP-EPDM-T20": "Resin",
    "MB-BLK": "Masterbatch",
    "P1": "Primer",
    "K1": "Clear",
    "H1": "Hardener",
    "T1": "Thinner",
    "BRK-SET": "Component",
    "SNS-HLD": "Component",
    "PKG-RT": "Packaging",
}
_PROCESS = {
    "Resin": "Molding",
    "Masterbatch": "Molding",
    "Primer": "Painting",
    "Basecoat": "Painting",
    "Clear": "Painting",
    "Hardener": "Painting",
    "Thinner": "Painting",
    "Component": "Assembly",
    "Packaging": "Packing",
}
_SCRAP = {"Resin": 2.0, "Masterbatch": 2.0, "Primer": 30.0, "Basecoat": 30.0, "Clear": 30.0}
_WAREHOUSE = {"Resin": "WH-RM", "Masterbatch": "WH-RM", "Component": "WH-CMP", "Packaging": "WH-CMP"}
_ON_HAND = {"MB-BLK": 9_000.0, "P1": 30_000.0, "K1": 24_000.0, "H1": 4_200.0, "T1": 40_000.0, "BRK-SET": 30_000.0, "SNS-HLD": 60_000.0, "PKG-RT": 120_000.0}
_FAKE_PO = [{"po_id": "PO-STANDIN-001", "material_id": "B-C07", "vendor_id": "VEN-WARNA", "qty": 120.0, "promised_date": TODAY + timedelta(days=7)}]
TXN_TYPES = ("Receipt", "Issue", "Adjust", "Reserve", "Release")


def _colors() -> list[str]:
    return sorted({c for p in plant.PRODUCTS for c in p.colors})


@lru_cache(maxsize=1)
def _materials() -> tuple[dict[str, Any], ...]:
    out = []
    for m in plant.MATERIALS:
        if m.material_id == "B-BASE":
            continue
        group = _GROUP[m.material_id]
        out.append(_row(m.material_id, m.material_name, group, None, m.unit, m.unit_cost_idr, m.safety_stock))
    out.extend(_row(f"B-{c}", f"Basecoat colour {c}", "Basecoat", c, "kg", 165_000, 40.0) for c in _colors())
    return tuple(out)


def _row(mid: str, name: str, group: str, color: str | None, unit: str, cost: int, safety: float) -> dict[str, Any]:
    vendor = plant.MATERIAL_VENDORS.get(mid, plant.MATERIAL_VENDORS["B-BASE"])[0]
    return {
        "material_id": mid,
        "material_name": name,
        "material_group": group,
        "color": color,
        "unit": unit,
        "unit_cost_idr": cost,
        "safety_stock": safety,
        "reorder_point": round(safety * 1.5, 1),
        "lead_time_days": int(plant.VENDOR_BY_ID[vendor]["lead_time_days"]),
        "default_vendor_id": vendor,
        "default_warehouse_id": _WAREHOUSE.get(group, "WH-PNT"),
    }


def list_materials(group: str | None = None) -> list[dict[str, Any]]:
    return [dict(m) for m in _materials() if group is None or m["material_group"] == group]


def get_material(material_id: str) -> dict[str, Any]:
    m = next((x for x in _materials() if x["material_id"] == material_id), None)
    if m is None:
        raise FVError(UNKNOWN, f"Material {material_id} does not exist.", {"material_id": material_id})
    return dict(m)


def list_warehouses() -> list[dict[str, Any]]:
    return [
        {"warehouse_id": "WH-RM", "warehouse_name": "Resin silo"},
        {"warehouse_id": "WH-PNT", "warehouse_name": "Paint store"},
        {"warehouse_id": "WH-CMP", "warehouse_name": "Components and packaging"},
    ]


def model_colors() -> dict[str, list[str]]:
    return {p.model: list(p.colors) for p in plant.PRODUCTS}


def sku_colors(sku: str) -> list[str]:
    return next((list(p.colors) for p in plant.PRODUCTS if p.sku == sku), [])


def _items(sku: str) -> list[dict[str, Any]]:
    by_id = {m["material_id"]: m for m in _materials()}
    out: list[dict[str, Any]] = []
    for b in plant.bom_lines():
        if b["sku"] != sku:
            continue
        mid = str(b["material_id"])
        qty = float(b["qty_per_unit"])  # type: ignore[arg-type]
        targets = [(f"B-{c}", c) for c in sku_colors(sku)] if mid == "B-BASE" else [(mid, None)]
        for tid, color in targets:
            m = by_id[tid]
            g = m["material_group"]
            out.append(
                {
                    "bom_id": f"BOM-2027-{plant.SKUS.index(sku) + 1:03d}",
                    "material_id": tid,
                    "material_name": m["material_name"],
                    "material_group": g,
                    "qty_per_unit": qty,
                    "unit": m["unit"],
                    "scrap_pct": _SCRAP.get(g, 0.0),
                    "process": _PROCESS[g],
                    "color": color,
                }
            )
    return out


def list_boms(sku: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    out = []
    for s in plant.SKUS:
        if (sku and s != sku) or (status and status != "Active"):
            continue
        out.append(
            {
                "bom_id": f"BOM-2027-{plant.SKUS.index(s) + 1:03d}",
                "sku": s,
                "version": 1,
                "status": "Active",
                "effective_date": "2027-01-01",
                "items": _items(s),
            }
        )
    return out


def active_bom(sku: str) -> dict[str, Any]:
    boms = list_boms(sku, "Active")
    if not boms:
        raise FVError(UNKNOWN, f"No active BOM for {sku}.", {"sku": sku})
    return boms[0]


def bom_items_for(sku: str, color: str | None) -> list[dict[str, Any]]:
    items = active_bom(sku)["items"]
    keep = [i for i in items if i["color"] is None or i["color"] == color]
    return [{**i, "basecoat_unresolved": color is None} for i in keep]


def color_split(sku: str, qty: float) -> dict[str, int]:
    colors = sku_colors(sku)
    total = round(qty)
    if not colors:
        return {}
    rows = warehouse.query(
        ("SELECT color, hourly_use AS h FROM fv.fact_stock_snapshot WHERE sku = :s AND snapshot_ts = (SELECT MAX(snapshot_ts) FROM fv.fact_stock_snapshot)"),
        {"s": sku},
    )
    use = {r["color"]: float(r["h"] or 0) for r in rows if r["color"] in colors}
    weights = [use.get(c, 0.0) for c in colors]
    if sum(weights) <= 0:
        weights = [1.0] * len(colors)
    raw = [total * w / sum(weights) for w in weights]
    parts = [int(x) for x in raw]
    for i in sorted(range(len(colors)), key=lambda k: raw[k] - parts[k], reverse=True)[: total - sum(parts)]:
        parts[i] += 1
    return dict(zip(colors, parts, strict=True))


def _avg_delay(vendor_id: str | None) -> float:
    v = plant.VENDOR_BY_ID.get(vendor_id or "")
    return float(v["avg_delay_days"]) if v else 0.0


def incoming(sid: str, material_id: str | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rows = warehouse.query("SELECT po_id, vendor_id, material_id, order_qty, promised_date FROM fv.fact_purchase_order WHERE po_status = 'Open'")
    fake = [{**p, "order_qty": p["qty"]} for p in _FAKE_PO]
    for po in [*rows, *fake]:
        d = _avg_delay(po["vendor_id"])
        promised = to_date(po["promised_date"])
        out.append(
            {
                "po_id": po["po_id"],
                "material_id": po["material_id"],
                "vendor_id": po["vendor_id"],
                "qty": float(po["order_qty"]),
                "promised_date": promised.isoformat(),
                "expected_date": (promised + timedelta(days=round(d))).isoformat(),
                "avg_delay_days": d,
                "source": "open_po",
            }
        )
    for r in effects.overrides(sid).extra_receipts:
        exp = to_date(r["expected_date"]).isoformat()
        out.append(
            {
                "po_id": r.get("po_id") or "proposed",
                "material_id": r.get("material_id", "PP-EPDM-T20"),
                "vendor_id": r.get("vendor_id"),
                "qty": float(r["qty"]),
                "promised_date": exp,
                "expected_date": exp,
                "avg_delay_days": 0.0,
                "source": "approved_po",
            }
        )
    return [r for r in out if material_id is None or r["material_id"] == material_id]


def _resin_on_hand() -> float:
    r = warehouse.query("SELECT on_hand_qty FROM fv.fact_material_stock WHERE material_id = 'PP-EPDM-T20' ORDER BY snapshot_date DESC LIMIT 1")
    return float(r[0]["on_hand_qty"]) if r else 0.0


def _seed_on_hand(m: dict[str, Any]) -> float:
    if m["material_id"] == "PP-EPDM-T20":
        return _resin_on_hand()
    if m["material_group"] == "Basecoat":
        return 615.0 if m["material_id"] == "B-C07" else 1_200.0
    return _ON_HAND.get(m["material_id"], 0.0)


_SEED_RESERVED = {"B-C07": 25.0}


def _reserved(sid: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for r in store().where(RESERVATIONS, "session_id", sid):
        if r["status"] == "Active":
            k = (r["material_id"], r["warehouse_id"])
            out[k] = out.get(k, 0.0) + float(r["qty"])
    return out


def stock(sid: str, material_id: str | None = None, warehouse_id: str | None = None) -> list[dict[str, Any]]:
    reserved = _reserved(sid)
    inc = incoming(sid)
    out = []
    for m in _materials():
        wh = m["default_warehouse_id"]
        if (material_id and m["material_id"] != material_id) or (warehouse_id and wh != warehouse_id):
            continue
        on = _seed_on_hand(m)
        res = _SEED_RESERVED.get(m["material_id"], 0.0) + reserved.get((m["material_id"], wh), 0.0)
        mine = [r for r in inc if r["material_id"] == m["material_id"]]
        iq = sum(r["qty"] for r in mine)
        out.append(
            {
                **{k: m[k] for k in ("material_id", "material_name", "material_group", "color", "unit")},
                "warehouse_id": wh,
                "on_hand_qty": on,
                "reserved_qty": res,
                "incoming_qty": iq,
                "incoming": mine,
                "available_qty": on - res + iq,
                "reorder_point": m["reorder_point"],
                "safety_stock": m["safety_stock"],
                "below_reorder": on - res < m["reorder_point"],
                "unit_cost_idr": m["unit_cost_idr"],
                "value_idr": round(on * m["unit_cost_idr"]),
                "default_vendor_id": m["default_vendor_id"],
                "lead_time_days": m["lead_time_days"],
                "snapshot_date": TODAY.isoformat(),
            }
        )
    return out


def record_transaction(sid: str, row: dict[str, Any]) -> dict[str, Any]:
    if row.get("type") not in TXN_TYPES:
        msg = f"transaction type must be one of {TXN_TYPES}"
        raise ValueError(msg)
    m = get_material(str(row["material_id"]))
    n = len(store().where(TRANSACTIONS, "session_id", sid)) + 1
    doc = {
        "txn_id": row.get("txn_id") or f"TXN-{TODAY:%y%m%d}-S{n:04d}",
        "session_id": sid,
        "date": row.get("date") or TODAY.isoformat(),
        "material_id": row["material_id"],
        "warehouse_id": row.get("warehouse_id") or m["default_warehouse_id"],
        "type": row["type"],
        "qty": float(row["qty"]),
        "balance_after": row.get("balance_after"),
        "ref": row.get("ref"),
    }
    store().set(TRANSACTIONS, f"{sid}:{doc['txn_id']}", doc)
    return doc


def transactions(sid: str, material_id: str | None = None) -> list[dict[str, Any]]:
    rows = [
        {
            "txn_id": r["move_id"],
            "date": to_date(r["move_date"]).isoformat(),
            "material_id": r["material_id"],
            "warehouse_id": "WH-RM",
            "type": r["move_type"],
            "qty": float(r["quantity"]),
            "balance_after": None,
            "ref": r["reference_id"],
        }
        for r in warehouse.query("SELECT move_id, move_date, material_id, move_type, quantity, reference_id FROM fv.fact_inventory_move ORDER BY move_ts")
    ]
    rows += [
        {k: t.get(k) for k in ("txn_id", "date", "material_id", "warehouse_id", "type", "qty", "balance_after", "ref")}
        for t in store().where(TRANSACTIONS, "session_id", sid)
    ]
    return [r for r in rows if material_id is None or r["material_id"] == material_id]


def clear(sid: str) -> None:
    for r in store().where(RESERVATIONS, "session_id", sid):
        store().delete(RESERVATIONS, f"{sid}:{r['reservation_id']}")
    for t in store().where(TRANSACTIONS, "session_id", sid):
        store().delete(TRANSACTIONS, f"{sid}:{t['txn_id']}")
