from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from typing import Any

from app import effects, plant, warehouse
from app.errors import FVError
from app.store import store
from app.tools.common import TODAY, to_date
from app.tools.material import delays_by_vendor
from app.tools.orders import product_name

NO_ACTIVE_BOM = "FV-MAT-001"
UNKNOWN_MATERIAL = "FV-MAT-002"
INVALID_COLOR = "FV-MAT-003"
UNKNOWN_WAREHOUSE = "FV-MAT-004"
UNKNOWN_BOM = "FV-MAT-005"
UNKNOWN_SKU = "FV-MAT-006"
BOM_BAD_MATERIAL = "FV-MAT-007"
UNKNOWN_GROUP = "FV-MAT-008"
UNKNOWN_BOM_STATUS = "FV-MAT-009"

RESERVATIONS = "material_reservations"
TRANSACTIONS = "material_transactions"
TXN_TYPES = ("Receipt", "Issue", "Adjust", "Reserve", "Release", "Incoming")

MATERIAL_SQL = """
SELECT material_id, material_name, material_type, material_group, color, unit, unit_cost_idr, safety_stock, reorder_point,
  lead_time_days, default_vendor_id, default_warehouse_id
FROM fv.dim_material ORDER BY material_id
"""
BOM_HEADER_SQL = "SELECT bom_id, sku, version, status, effective_date FROM fv.bom_header ORDER BY bom_id"
BOM_ITEM_SQL = "SELECT item_id, bom_id, line_no, material_id, qty_per_unit, unit, scrap_pct, process, color FROM fv.bom_item ORDER BY bom_id, line_no"
STOCK_SQL = """
SELECT material_id, warehouse_id, on_hand_qty, reserved_qty, snapshot_date FROM fv.fact_material_stock
WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM fv.fact_material_stock)
"""
OPEN_PO_SQL = (
    "SELECT po_id, vendor_id, material_id, order_qty, promised_date FROM fv.fact_purchase_order WHERE po_status = 'Open' ORDER BY promised_date, po_id"
)
MOVE_SQL = "SELECT move_id, move_date, material_id, move_type, quantity, reference_id FROM fv.fact_inventory_move ORDER BY move_ts, move_id"
SNAPSHOT_SQL = "SELECT sku, color, hourly_use FROM fv.fact_stock_snapshot WHERE snapshot_ts = (SELECT MAX(snapshot_ts) FROM fv.fact_stock_snapshot)"
FIRST_STOCK_SQL = (
    "SELECT material_id, on_hand_qty, snapshot_date FROM fv.fact_material_stock WHERE snapshot_date = (SELECT MIN(snapshot_date) FROM fv.fact_material_stock)"
)


def _iso(v: object) -> str | None:
    return None if v is None else to_date(v).isoformat()


@lru_cache(maxsize=1)
def _materials() -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            **r,
            "unit_cost_idr": int(r["unit_cost_idr"] or 0),
            "safety_stock": float(r["safety_stock"] or 0),
            "reorder_point": float(r["reorder_point"] or 0),
            "lead_time_days": int(r["lead_time_days"] or 0),
        }
        for r in warehouse.query(MATERIAL_SQL)
    )


@lru_cache(maxsize=1)
def _boms() -> tuple[dict[str, Any], ...]:
    mats = {m["material_id"]: m for m in _materials()}
    items: dict[str, list[dict[str, Any]]] = {}
    for r in warehouse.query(BOM_ITEM_SQL):
        m = mats.get(r["material_id"])
        items.setdefault(r["bom_id"], []).append(
            {
                **r,
                "line_no": int(r["line_no"] or 0),
                "qty_per_unit": float(r["qty_per_unit"]),
                "scrap_pct": float(r["scrap_pct"] or 0),
                "material_name": m["material_name"] if m else None,
                "material_group": m["material_group"] if m else None,
            }
        )
    out = []
    for h in warehouse.query(BOM_HEADER_SQL):
        its = items.get(h["bom_id"], [])
        out.append(
            {
                **h,
                "effective_date": _iso(h["effective_date"]),
                "product_name": product_name(str(h["sku"])) if h["sku"] in plant.PRODUCT_BY_SKU else h["sku"],
                "colors": sorted({str(i["color"]) for i in its if i["color"]}),
                "item_count": len(its),
                "items": its,
            }
        )
    return tuple(out)


def reset_cache() -> None:
    _materials.cache_clear()
    _boms.cache_clear()


def list_materials(group: str | None = None) -> list[dict[str, Any]]:
    if group and group not in plant.MATERIAL_GROUPS:
        raise FVError(UNKNOWN_GROUP, f"Unknown material group {group}.", {"group": group, "valid": list(plant.MATERIAL_GROUPS)})
    return [dict(m) for m in _materials() if not group or m["material_group"] == group]


def get_material(material_id: str) -> dict[str, Any]:
    m = next((m for m in _materials() if m["material_id"] == material_id), None)
    if m is None:
        raise FVError(UNKNOWN_MATERIAL, f"Unknown material {material_id}.", {"material_id": material_id})
    return dict(m)


def list_warehouses() -> list[dict[str, Any]]:
    return [{"warehouse_id": w.warehouse_id, "warehouse_name": w.warehouse_name, "warehouse_type": w.warehouse_type} for w in plant.WAREHOUSES]


def _warehouse(warehouse_id: str) -> None:
    if warehouse_id not in plant.WAREHOUSE_BY_ID:
        raise FVError(UNKNOWN_WAREHOUSE, f"Unknown warehouse {warehouse_id}.", {"warehouse_id": warehouse_id, "valid": list(plant.WAREHOUSE_BY_ID)})


def _sku(sku: str) -> plant.Product:
    p = plant.PRODUCT_BY_SKU.get(sku)
    if p is None:
        raise FVError(UNKNOWN_SKU, f"Unknown product {sku}.", {"sku": sku, "valid": plant.SKUS})
    return p


def model_colors() -> dict[str, list[str]]:
    return plant.model_colors()


def sku_colors(sku: str) -> list[str]:
    return list(_sku(sku).colors)


def check_color(sku: str, color: str | None) -> None:
    if color is not None and color not in sku_colors(sku):
        raise FVError(INVALID_COLOR, f"Colour {color} is not made for {sku}.", {"sku": sku, "color": color, "valid": sku_colors(sku)})


def color_catalog() -> list[dict[str, Any]]:
    models = {c: [m for m, cs in plant.MODEL_COLORS.items() if c in cs] for c in plant.BASECOAT_COLORS}
    return [
        {
            "color": c,
            "family": plant.COLOR_FAMILY[c],
            "basecoat_id": f"B-{c}",
            "thinner_id": plant.FAMILY_THINNER[plant.COLOR_FAMILY[c]],
            "models": models[c],
        }
        for c in plant.BASECOAT_COLORS
    ]


def list_boms(sku: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    if sku:
        _sku(sku)
    if status and status not in plant.BOM_STATUSES:
        raise FVError(UNKNOWN_BOM_STATUS, f"Unknown BOM status {status}.", {"status": status, "valid": list(plant.BOM_STATUSES)})
    return [_copy(b) for b in _boms() if (not sku or b["sku"] == sku) and (not status or b["status"] == status)]


def _copy(b: dict[str, Any]) -> dict[str, Any]:
    return {**b, "items": [dict(i) for i in b["items"]]}


def get_bom(bom_id: str) -> dict[str, Any]:
    b = next((b for b in _boms() if b["bom_id"] == bom_id), None)
    if b is None:
        raise FVError(UNKNOWN_BOM, f"Unknown BOM {bom_id}.", {"bom_id": bom_id})
    return _copy(b)


def active_bom(sku: str) -> dict[str, Any]:
    live = [b for b in _boms() if b["sku"] == sku and b["status"] == "Active" and (b["effective_date"] or "") <= TODAY.isoformat()]
    if not live:
        raise FVError(NO_ACTIVE_BOM, f"No active BOM for {sku}.", {"sku": sku})
    b = max(live, key=lambda x: str(x["effective_date"]))
    bad = [i["material_id"] for i in b["items"] if i["material_name"] is None]
    if bad:
        raise FVError(
            BOM_BAD_MATERIAL, f"BOM {b['bom_id']} for {sku} uses unknown material {', '.join(bad)}.", {"sku": sku, "bom_id": b["bom_id"], "materials": bad}
        )
    return _copy(b)


def bom_items_for(sku: str, color: str | None) -> list[dict[str, Any]]:
    check_color(sku, color)
    b = active_bom(sku)
    keep = [i for i in b["items"] if i["color"] is None or i["color"] == color]
    return [{**i, "bom_id": b["bom_id"], "basecoat_unresolved": color is None} for i in keep]


def color_split(sku: str, qty: float) -> dict[str, int]:
    colors = sku_colors(sku)
    use = {str(r["color"]): float(r["hourly_use"] or 0) for r in warehouse.query(SNAPSHOT_SQL) if r["sku"] == sku and r["color"] in colors}
    weights = [use.get(c, 0.0) for c in colors]
    if sum(weights) <= 0:
        weights = [1.0] * len(colors)
    total = round(qty)
    raw = [total * w / sum(weights) for w in weights]
    parts = [int(x) for x in raw]
    order = sorted(range(len(colors)), key=lambda k: (-(raw[k] - parts[k]), k))
    for k in order[: total - sum(parts)]:
        parts[k] += 1
    return dict(zip(colors, parts, strict=True))


def vendor_delay(vendor_id: str | None, o: effects.Overrides, delays: dict[str, float]) -> float:
    return max(0.0, float(delays.get(str(vendor_id), 0.0))) + float(o.vendor_extra_delay.get(str(vendor_id), 0.0))


def expected_after(promised: object, delay: float) -> str:
    return (to_date(promised) + timedelta(days=round(delay))).isoformat()


def incoming(sid: str, material_id: str | None = None) -> list[dict[str, Any]]:
    from app import purchasing_docs

    o = effects.overrides(sid)
    delays = delays_by_vendor()
    got = purchasing_docs.received_by_po(sid)
    rows: list[dict[str, Any]] = []
    for po in warehouse.query(OPEN_PO_SQL):
        if material_id and po["material_id"] != material_id:
            continue
        qty = float(po["order_qty"]) - got.get((str(po["po_id"]), str(po["material_id"])), 0.0)
        if qty <= purchasing_docs.EPS:
            continue
        delay = vendor_delay(po["vendor_id"], o, delays)
        promised = to_date(po["promised_date"])
        rows.append(
            {
                "po_id": po["po_id"],
                "material_id": po["material_id"],
                "vendor_id": po["vendor_id"],
                "qty": round(qty, 4),
                "promised_date": promised.isoformat(),
                "expected_date": expected_after(promised, delay),
                "avg_delay_days": round(delay, 1),
                "source": "open_po",
            }
        )
    for ln in purchasing_docs.open_lines(sid):
        if material_id and ln["material_id"] != material_id:
            continue
        delay = vendor_delay(ln["vendor_id"], o, delays)
        rows.append(
            {
                "po_id": ln["po_id"],
                "material_id": ln["material_id"],
                "vendor_id": ln["vendor_id"],
                "qty": round(ln["qty"], 4),
                "promised_date": ln["promised_date"],
                "expected_date": expected_after(ln["promised_date"], delay),
                "avg_delay_days": round(delay, 1),
                "source": "session_po",
            }
        )
    for r in o.extra_receipts:
        mid = r.get("material_id") or "PP-EPDM-T20"
        if material_id and mid != material_id:
            continue
        exp = to_date(r["expected_date"]).isoformat()
        rows.append(
            {
                "po_id": r.get("po_id") or "approved",
                "material_id": mid,
                "vendor_id": r.get("vendor_id"),
                "qty": float(r["qty"]),
                "promised_date": exp,
                "expected_date": exp,
                "avg_delay_days": 0.0,
                "source": "approved_po",
            }
        )
    return sorted(rows, key=lambda x: (x["expected_date"], x["po_id"]))


def _session_reserved(sid: str) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for r in store().where(RESERVATIONS, "session_id", sid):
        if r.get("status") != "Active":
            continue
        mid = str(r["material_id"])
        m = plant.MATERIAL_BY_ID.get(mid)
        wh = str(r.get("warehouse_id") or (m.default_warehouse_id if m else ""))
        out[(mid, wh)] = out.get((mid, wh), 0.0) + float(r.get("qty") or 0)
    return out


def stock(sid: str, material_id: str | None = None, warehouse_id: str | None = None) -> list[dict[str, Any]]:
    if material_id:
        get_material(material_id)
    if warehouse_id:
        _warehouse(warehouse_id)
    mats = {m["material_id"]: m for m in _materials()}
    snap = {
        (r["material_id"], r["warehouse_id"] or mats[r["material_id"]]["default_warehouse_id"]): r
        for r in warehouse.query(STOCK_SQL)
        if r["material_id"] in mats
    }
    from app import purchasing_docs

    received = purchasing_docs.received_on_hand(sid)
    keys = set(snap) | {(m["material_id"], m["default_warehouse_id"]) for m in mats.values()} | {k for k in received if k[0] in mats}
    held = _session_reserved(sid)
    inc_all = incoming(sid)
    rows = []
    for mid, wh in sorted(keys, key=lambda k: (plant.MATERIAL_GROUPS.index(mats[k[0]]["material_group"]), k[0], k[1])):
        if (material_id and mid != material_id) or (warehouse_id and wh != warehouse_id):
            continue
        m, s = mats[mid], snap.get((mid, wh), {})
        on_hand = float(s.get("on_hand_qty") or 0) + received.get((mid, wh), 0.0)
        reserved = float(s.get("reserved_qty") or 0) + held.get((mid, wh), 0.0)
        inc = [i for i in inc_all if i["material_id"] == mid] if wh == m["default_warehouse_id"] else []
        inc_qty = sum(i["qty"] for i in inc)
        rows.append(
            {
                "material_id": mid,
                "material_name": m["material_name"],
                "material_group": m["material_group"],
                "color": m["color"],
                "unit": m["unit"],
                "warehouse_id": wh,
                "snapshot_date": _iso(s.get("snapshot_date")),
                "on_hand_qty": round(on_hand, 1),
                "reserved_qty": round(reserved, 1),
                "incoming_qty": round(inc_qty, 1),
                "incoming": inc,
                "available_qty": round(on_hand - reserved + inc_qty, 1),
                "reorder_point": m["reorder_point"],
                "safety_stock": m["safety_stock"],
                "below_reorder": on_hand - reserved < m["reorder_point"],
                "unit_cost_idr": m["unit_cost_idr"],
                "value_idr": round(on_hand * m["unit_cost_idr"]),
                "default_vendor_id": m["default_vendor_id"],
                "lead_time_days": m["lead_time_days"],
            }
        )
    return rows


def inventory(sid: str, material_id: str | None = None, warehouse_id: str | None = None, group: str | None = None) -> dict[str, Any]:
    if group and group not in plant.MATERIAL_GROUPS:
        raise FVError(UNKNOWN_GROUP, f"Unknown material group {group}.", {"group": group, "valid": list(plant.MATERIAL_GROUPS)})
    rows = [r for r in stock(sid, material_id, warehouse_id) if not group or r["material_group"] == group]
    return {
        "as_of": TODAY.isoformat(),
        "rows": rows,
        "kpis": {
            "materials": len({r["material_id"] for r in rows}),
            "below_reorder": sum(1 for r in rows if r["below_reorder"]),
            "incoming_pos": len({i["po_id"] for r in rows for i in r["incoming"]}),
            "value_idr": sum(r["value_idr"] for r in rows),
        },
        "formula": "Available = On Hand - Reserved + Incoming",
        "source": "fv.fact_material_stock, fv.dim_material, fv.fact_purchase_order (open), session POs, goods receipts and reservations of this session",
    }


def record_transaction(sid: str, row: dict[str, Any]) -> dict[str, Any]:
    if row.get("type") not in TXN_TYPES:
        msg = f"transaction type must be one of {TXN_TYPES}"
        raise ValueError(msg)
    get_material(str(row["material_id"]))
    n = len(store().where(TRANSACTIONS, "session_id", sid)) + 1
    doc = {
        "txn_id": row.get("txn_id") or f"TXN-{TODAY:%y%m%d}-S{n:04d}",
        "session_id": sid,
        "date": row.get("date") or TODAY.isoformat(),
        "material_id": row["material_id"],
        "warehouse_id": row.get("warehouse_id") or plant.MATERIAL_BY_ID[str(row["material_id"])].default_warehouse_id,
        "type": row["type"],
        "qty": float(row["qty"]),
        "balance_after": row.get("balance_after"),
        "ref": row.get("ref"),
    }
    store().set(TRANSACTIONS, f"{sid}:{doc['txn_id']}", doc)
    return doc


def transactions(sid: str, material_id: str | None = None) -> list[dict[str, Any]]:
    if material_id:
        get_material(material_id)
    mats = {m["material_id"]: m for m in _materials()}
    moves = warehouse.query(MOVE_SQL)
    moved = {str(r["material_id"]) for r in moves}
    rows: list[dict[str, Any]] = []
    balance: dict[str, float] = {}
    first = {str(r["material_id"]): r for r in warehouse.query(FIRST_STOCK_SQL)}
    for mid, r in first.items():
        if mid in moved:
            day_moves = sum(float(m["quantity"]) for m in moves if m["material_id"] == mid and to_date(m["move_date"]) == to_date(r["snapshot_date"]))
            balance[mid] = float(r["on_hand_qty"] or 0) - day_moves
    for r in moves:
        mid = str(r["material_id"])
        if material_id and mid != material_id:
            continue
        q = float(r["quantity"])
        balance[mid] = balance.get(mid, 0.0) + q
        rows.append(
            {
                "txn_id": r["move_id"],
                "date": _iso(r["move_date"]),
                "material_id": mid,
                "warehouse_id": mats[mid]["default_warehouse_id"] if mid in mats else None,
                "type": r["move_type"] if r["move_type"] in TXN_TYPES else "Adjust",
                "qty": round(q, 1),
                "balance_after": round(balance[mid], 1),
                "ref": r["reference_id"],
                "source": "fv.fact_inventory_move",
            }
        )
    for s in warehouse.query(STOCK_SQL):
        mid = str(s["material_id"])
        if mid in moved or (material_id and mid != material_id):
            continue
        q = float(s["on_hand_qty"] or 0)
        balance[mid] = q
        rows.append(
            {
                "txn_id": f"OPEN-{mid}",
                "date": _iso(s["snapshot_date"]),
                "material_id": mid,
                "warehouse_id": s["warehouse_id"],
                "type": "Adjust",
                "qty": round(q, 1),
                "balance_after": round(q, 1),
                "ref": "Opening balance (stock take)",
                "source": "fv.fact_material_stock",
            }
        )
    for d in sorted(store().where(TRANSACTIONS, "session_id", sid), key=lambda x: (str(x.get("date")), str(x.get("txn_id")))):
        mid = str(d["material_id"])
        if material_id and mid != material_id:
            continue
        q = float(d.get("qty") or 0)
        if d.get("type") in ("Receipt", "Issue", "Adjust"):
            balance[mid] = balance.get(mid, 0.0) + q
        after = d.get("balance_after")
        rows.append(
            {
                **{k: d.get(k) for k in ("txn_id", "date", "material_id", "warehouse_id", "type", "ref")},
                "qty": round(q, 1),
                "balance_after": round(float(after), 1) if after is not None else round(balance.get(mid, 0.0), 1),
                "source": "session",
            }
        )
    return sorted(rows, key=lambda x: (str(x["date"]), x["source"] == "session", str(x["txn_id"])))


def clear(sid: str) -> None:
    for d in store().where(TRANSACTIONS, "session_id", sid):
        store().delete(TRANSACTIONS, f"{sid}:{d['txn_id']}")
