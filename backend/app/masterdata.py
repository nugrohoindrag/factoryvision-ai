"""Master data: the plant's dimension tables, grouped the way the console's Master Data menu groups them.

The seed is the base and stays untouched: the warehouse is read through allowlisted SELECTs only. Create, edit and
delete are kept per session in the store (`master_edits`, one document per changed row) and laid over the seed when the
rows are read, so each judge's session has its own master data and Reset returns it to the seed. The other modules
still compute from the seed; these edits show here only. Product routings are derived (plans.routing), so read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app import plans, warehouse
from app.errors import FVError
from app.store import store

UNKNOWN_ENTITY = "FV-DATA-001"
INVALID = "FV-MD-001"
DUPLICATE = "FV-MD-002"
UNKNOWN_ROW = "FV-MD-003"
REFERENCED = "FV-MD-004"
READ_ONLY = "FV-MD-005"

EDITS = "master_edits"
BOM_HEADERS = "bom-headers"  # a reference target that is not a menu entity: the versioned BOM headers
HOURS_PER_DAY = 24


@dataclass(frozen=True)
class Col:
    key: str
    label: str
    kind: str = "str"  # str | int | float
    ref: str | None = None  # entity key whose ids this column must name
    required: bool = False
    derived: bool = False  # filled by the server from a reference, not typed


def num(key: str, label: str, **kw: Any) -> Col:
    return Col(key, label, "float", **kw)


def whole(key: str, label: str, **kw: Any) -> Col:
    return Col(key, label, "int", **kw)


@dataclass(frozen=True)
class Entity:
    key: str
    label: str
    section: str
    pk: tuple[str, ...]
    columns: tuple[Col, ...]
    sql: str | None
    source: str
    editable: bool = True


ENTITIES: tuple[Entity, ...] = (
    Entity(
        "customers",
        "Customers",
        "Partners",
        ("customer_id",),
        (
            Col("customer_id", "ID"),
            Col("customer_name", "Name", required=True),
            Col("customer_type", "Type"),
            whole("penalty_idr_per_day", "Late penalty / day (Rp)"),
            Col("pic_name", "Contact"),
            Col("pic_contact", "Contact email"),
            Col("delivery_address", "Delivery address"),
            Col("dock_number", "Dock"),
        ),
        "SELECT * FROM fv.dim_customer ORDER BY customer_id",
        "fv.dim_customer",
    ),
    Entity(
        "vendors",
        "Vendors",
        "Partners",
        ("vendor_id",),
        (
            Col("vendor_id", "ID"),
            Col("vendor_name", "Name", required=True),
            Col("category", "Category"),
            whole("lead_time_days", "Lead time (days)"),
            whole("unit_price_idr", "Unit price (Rp)"),
        ),
        "SELECT * FROM fv.dim_vendor ORDER BY vendor_id",
        "fv.dim_vendor",
    ),
    Entity(
        "products",
        "Products",
        "Products & Process",
        ("sku",),
        (
            Col("sku", "SKU"),
            Col("model", "Model", required=True),
            Col("segment", "Segment"),
            Col("position", "Position"),
            num("ideal_cycle_s", "Ideal cycle (s)"),
            num("shot_weight_kg", "Shot weight (kg)"),
            whole("price_idr", "Price (Rp)"),
            Col("colors", "Colours"),
        ),
        "SELECT * FROM fv.dim_product ORDER BY sku",
        "fv.dim_product",
    ),
    Entity(
        "materials",
        "Materials",
        "Products & Process",
        ("material_id",),
        (
            Col("material_id", "ID"),
            Col("material_name", "Name", required=True),
            Col("material_type", "Type"),
            Col("material_group", "Group"),
            Col("color", "Colour"),
            Col("unit", "Unit", required=True),
            whole("unit_cost_idr", "Unit cost (Rp)"),
            num("safety_stock", "Safety stock"),
            num("reorder_point", "Reorder point"),
            whole("lead_time_days", "Lead time (days)"),
            Col("default_vendor_id", "Default vendor", ref="vendors"),
            Col("default_warehouse_id", "Default warehouse", ref="warehouses"),
        ),
        "SELECT * FROM fv.dim_material ORDER BY material_group, material_id",
        "fv.dim_material",
    ),
    Entity(
        "bom",
        "Bill of Materials",
        "Products & Process",
        ("item_id",),
        (
            Col("item_id", "Item"),
            Col("bom_id", "BOM", ref=BOM_HEADERS, required=True),
            Col("sku", "SKU", derived=True),
            Col("version", "Version", derived=True),
            Col("status", "Status", derived=True),
            whole("line_no", "Line", required=True),
            Col("material_id", "Material", ref="materials", required=True),
            Col("material_name", "Material name", derived=True),
            Col("process", "Process"),
            Col("color", "Colour"),
            num("qty_per_unit", "Qty per unit", required=True),
            Col("unit", "Unit"),
            num("scrap_pct", "Scrap %"),
        ),
        "SELECT i.item_id, h.bom_id, h.sku, h.version, h.status, i.line_no, i.material_id, m.material_name, i.process, i.color, i.qty_per_unit, i.unit,"
        " i.scrap_pct FROM fv.bom_item i JOIN fv.bom_header h ON h.bom_id = i.bom_id LEFT JOIN fv.dim_material m ON m.material_id = i.material_id"
        " ORDER BY h.sku, h.version, i.line_no",
        "fv.bom_header, fv.bom_item, fv.dim_material",
    ),
    Entity(
        "process-flow",
        "Process Flow",
        "Products & Process",
        ("seq",),
        (whole("seq", "Step"), Col("process", "Process", required=True), Col("machine_id", "Machine", ref="machines")),
        "SELECT * FROM fv.routing ORDER BY seq",
        "fv.routing",
    ),
    Entity(
        "routings",
        "Product Routings",
        "Products & Process",
        ("sku", "sequence"),
        (Col("sku", "SKU"), whole("sequence", "Step"), Col("process", "Process"), Col("resources", "Machines that can run it")),
        None,
        "fv.dim_product, fv.product_mold_compatibility (as the planner routes work orders)",
        editable=False,
    ),
    Entity(
        "machines",
        "Machines",
        "Facilities",
        ("machine_id",),
        (
            Col("machine_id", "ID"),
            Col("machine_name", "Name", required=True),
            Col("process", "Process", required=True),
            Col("area", "Area"),
            whole("tonnage", "Tonnage (t)"),
            num("kw_rated", "Rated kW"),
            num("cycle_alarm_s", "Cycle alarm (s)"),
        ),
        "SELECT * FROM fv.dim_machine ORDER BY machine_id",
        "fv.dim_machine",
    ),
    Entity(
        "molds",
        "Molds",
        "Facilities",
        ("mold_id",),
        (
            Col("mold_id", "ID"),
            Col("sku", "SKU", ref="products", required=True),
            whole("cavity", "Cavities"),
            Col("current_machine_id", "Mounted on", ref="machines"),
        ),
        "SELECT * FROM fv.dim_mold ORDER BY mold_id",
        "fv.dim_mold",
    ),
    Entity(
        "mold-compatibility",
        "Mold Compatibility",
        "Facilities",
        ("sku", "machine_id"),
        (Col("sku", "SKU", ref="products"), Col("mold_id", "Mold", ref="molds"), Col("machine_id", "Machine", ref="machines")),
        "SELECT * FROM fv.product_mold_compatibility ORDER BY sku, machine_id",
        "fv.product_mold_compatibility",
    ),
    Entity(
        "shifts",
        "Shifts",
        "Facilities",
        ("shift_id",),
        (
            Col("shift_id", "Shift"),
            whole("start_hour", "Start (h)", required=True),
            whole("end_hour", "End (h)", required=True),
            whole("planned_minutes", "Planned minutes"),
        ),
        "SELECT * FROM fv.dim_shift ORDER BY shift_id",
        "fv.dim_shift",
    ),
    Entity(
        "warehouses",
        "Warehouses",
        "Facilities",
        ("warehouse_id",),
        (Col("warehouse_id", "ID"), Col("warehouse_name", "Name", required=True), Col("warehouse_type", "Type")),
        "SELECT * FROM fv.dim_warehouse ORDER BY warehouse_id",
        "fv.dim_warehouse",
    ),
    Entity(
        "defects",
        "Defect Codes",
        "Classification",
        ("defect_code",),
        (
            Col("defect_code", "Code"),
            Col("defect_name", "Name", required=True),
            Col("shop_term", "Shop term"),
            Col("ng_group", "NG group"),
            Col("defect_category", "Category"),
            Col("default_disposition", "Default disposition"),
        ),
        "SELECT * FROM fv.dim_defect ORDER BY defect_category, defect_code",
        "fv.dim_defect",
    ),
    Entity(
        "downtime-reasons",
        "Downtime Reasons",
        "Classification",
        ("reason_code",),
        (
            Col("reason_code", "Code"),
            Col("reason_name", "Name", required=True),
            Col("reason_category", "Category"),
            Col("loss_category", "Loss category"),
            Col("shop_term", "Shop term"),
        ),
        "SELECT * FROM fv.dim_downtime_reason ORDER BY reason_category, reason_code",
        "fv.dim_downtime_reason",
    ),
)

BY_KEY = {e.key: e for e in ENTITIES}
BOM_HEADER_SQL = "SELECT bom_id, sku, version, status FROM fv.bom_header ORDER BY bom_id"


def entity(key: str) -> Entity:
    e = BY_KEY.get(key)
    if e is None:
        raise FVError(UNKNOWN_ENTITY, f"no master data called {key!r}")
    return e


def key_of(e: Entity, row: dict[str, Any]) -> str:
    """One string per row, the URL form of its key: the key fields joined by '|'."""
    return "|".join(str(row.get(k)) for k in e.pk)


# --- reading: seed rows with this session's edits laid over them ---


def _routings() -> list[dict[str, Any]]:
    skus = [r["sku"] for r in warehouse.query("SELECT sku FROM fv.dim_product ORDER BY sku")]
    return [{"sku": sku, **step, "resources": ", ".join(step["resources"])} for sku in skus for step in plans.routing(sku)]


def _base(e: Entity) -> list[dict[str, Any]]:
    return warehouse.query(e.sql) if e.sql else _routings()


def _edits(sid: str, e: Entity) -> dict[str, dict[str, Any]]:
    return {d["row_key"]: d for d in store().where(EDITS, "session_id", sid) if d["entity"] == e.key}


def _doc_id(sid: str, e: Entity, row_key: str) -> str:
    return f"{sid}:{e.key}:{row_key}"


def _merged(sid: str, e: Entity) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(rows, deleted seed rows). An edited or created row carries `_status`; rows created here come after the seed's."""
    edits = _edits(sid, e) if e.editable else {}
    rows: list[dict[str, Any]] = []
    deleted: list[dict[str, Any]] = []
    seen = set()
    for r in _base(e):
        k = key_of(e, r)
        seen.add(k)
        d = edits.get(k)
        if d is None:
            rows.append(r)
        elif d["op"] == "delete":
            deleted.append({**r, "_key": k})
        else:
            rows.append({**d["row"], "_status": "edited"})
    created = sorted((d for k, d in edits.items() if k not in seen and d["op"] == "upsert"), key=lambda d: str(d["at"]))
    rows += [{**d["row"], "_status": "created"} for d in created]
    for r in rows:
        r["_key"] = key_of(e, r)
    return rows, deleted


def rows(sid: str, e: Entity) -> list[dict[str, Any]]:
    return _merged(sid, e)[0]


def _ids(sid: str, ref: str) -> list[str]:
    if ref == BOM_HEADERS:
        return [str(h["bom_id"]) for h in warehouse.query(BOM_HEADER_SQL)]
    return [key_of(BY_KEY[ref], r) for r in rows(sid, BY_KEY[ref])]


def catalog(sid: str) -> dict[str, Any]:
    return {"entities": [{"key": e.key, "label": e.label, "section": e.section, "count": len(rows(sid, e))} for e in ENTITIES]}


def detail(sid: str, key: str) -> dict[str, Any]:
    e = entity(key)
    merged, deleted = _merged(sid, e)
    return {
        "key": e.key,
        "label": e.label,
        "section": e.section,
        "editable": e.editable,
        "pk": list(e.pk),
        "columns": [
            {"key": c.key, "label": c.label, "kind": c.kind, "ref": c.ref, "required": c.required or c.key in e.pk, "derived": c.derived} for c in e.columns
        ],
        "options": {c.key: _ids(sid, c.ref) for c in e.columns if c.ref and e.editable},
        "rows": merged,
        "deleted": deleted,
        "source": e.source,
    }


# --- writing: validated per session, never to the warehouse ---


def _clean(sid: str, e: Entity, body: dict[str, Any]) -> dict[str, Any]:
    """The row as stored: typed values, references checked, derived columns filled from what they reference."""
    out: dict[str, Any] = {}
    problems: list[dict[str, str]] = []
    for c in e.columns:
        if c.derived:
            continue
        v = body.get(c.key)
        if isinstance(v, str):
            v = v.strip()
        if v in (None, ""):
            if c.required or c.key in e.pk:
                problems.append({"field": c.key, "message": f"{c.label} is required."})
            out[c.key] = None
            continue
        if c.kind in ("int", "float"):
            try:
                n = float(v)
            except (TypeError, ValueError):
                problems.append({"field": c.key, "message": f"{c.label} must be a number."})
                continue
            if c.kind == "int" and not n.is_integer():
                problems.append({"field": c.key, "message": f"{c.label} must be a whole number."})
                continue
            if n < 0:
                problems.append({"field": c.key, "message": f"{c.label} cannot be negative."})
                continue
            out[c.key] = int(n) if c.kind == "int" else n
        else:
            out[c.key] = str(v)
    problems += [
        {"field": c.key, "message": f"{c.label} {out[c.key]} does not exist."}
        for c in e.columns
        if c.ref and out.get(c.key) is not None and str(out[c.key]) not in _ids(sid, c.ref)
    ]
    if e.key == "shifts":
        hours = ("start_hour", "end_hour")
        problems += [{"field": k, "message": "Use an hour from 0 to 24."} for k in hours if isinstance(out.get(k), int) and out[k] > HOURS_PER_DAY]
    if problems:
        raise FVError(INVALID, f"{len(problems)} field(s) need fixing", detail={"fields": problems})
    if e.key == "bom":
        header = next(h for h in warehouse.query(BOM_HEADER_SQL) if h["bom_id"] == out["bom_id"])
        material = next(r for r in rows(sid, BY_KEY["materials"]) if r["material_id"] == out["material_id"])
        out |= {"sku": header["sku"], "version": header["version"], "status": header["status"], "material_name": material.get("material_name")}
    return out


def _writable(key: str) -> Entity:
    e = entity(key)
    if not e.editable:
        raise FVError(READ_ONLY, f"{e.label} is derived and cannot be edited")
    return e


def _find(sid: str, e: Entity, row_key: str) -> dict[str, Any]:
    r = next((r for r in rows(sid, e) if r["_key"] == row_key), None)
    if r is None:
        raise FVError(UNKNOWN_ROW, f"{e.label} has no row {row_key}")
    return r


def _save(sid: str, e: Entity, row_key: str, op: str, row: dict[str, Any] | None, user: str) -> None:
    doc = {"session_id": sid, "entity": e.key, "row_key": row_key, "op": op, "row": row, "at": datetime.now(UTC).isoformat(), "by": user}
    store().set(EDITS, _doc_id(sid, e, row_key), doc)


def create(sid: str, key: str, body: dict[str, Any], user: str = "You") -> dict[str, Any]:
    e = _writable(key)
    row = _clean(sid, e, body)
    k = key_of(e, row)
    if any(r["_key"] == k for r in rows(sid, e)):
        raise FVError(DUPLICATE, f"{e.label} already has {k}", detail={"key": k})
    _save(sid, e, k, "upsert", row, user)
    return _find(sid, e, k)


def update(sid: str, key: str, row_key: str, body: dict[str, Any], user: str = "You") -> dict[str, Any]:
    e = _writable(key)
    current = _find(sid, e, row_key)
    # the key identifies the row, so it is kept as it is; everything else is replaced
    row = _clean(sid, e, {**body, **{k: current[k] for k in e.pk}})
    _save(sid, e, row_key, "upsert", row, user)
    return _find(sid, e, row_key)


def _references(sid: str, e: Entity, row_key: str) -> list[dict[str, Any]]:
    out = []
    for other in ENTITIES:
        cols = [c for c in other.columns if c.ref == e.key]
        for c in cols:
            hits = [r["_key"] for r in rows(sid, other) if str(r.get(c.key)) == row_key]
            if hits:
                out.append({"entity": other.key, "label": other.label, "field": c.key, "count": len(hits), "keys": hits[:5]})
    return out


def delete(sid: str, key: str, row_key: str, user: str = "You") -> dict[str, Any]:
    e = _writable(key)
    row = _find(sid, e, row_key)
    refs = _references(sid, e, row_key)
    if refs:
        names = ", ".join(f"{r['count']} in {r['label']}" for r in refs)
        raise FVError(REFERENCED, f"{row_key} is still used: {names}", detail={"references": refs})
    if row.get("_status") == "created":
        store().delete(EDITS, _doc_id(sid, e, row_key))  # it never existed in the seed: nothing to hide
    else:
        _save(sid, e, row_key, "delete", None, user)
    return {"deleted": row_key}


def revert(sid: str, key: str, row_key: str) -> dict[str, Any]:
    """Drop this session's edit of one row: an edited or deleted seed row comes back as seeded, a created one goes."""
    e = _writable(key)
    if store().get(EDITS, _doc_id(sid, e, row_key)) is None:
        raise FVError(UNKNOWN_ROW, f"{e.label} {row_key} has no change to revert")
    store().delete(EDITS, _doc_id(sid, e, row_key))
    return {"reverted": row_key}


def clear(sid: str) -> None:
    for d in store().where(EDITS, "session_id", sid):
        store().delete(EDITS, f"{sid}:{d['entity']}:{d['row_key']}")
