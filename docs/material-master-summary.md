# Material master data: summary

What the masterdata part of Material Readiness adds: rows MR-R01 to R07 (backend and data), MR-R40, R41 and R44, and the backend half of MR-R04. Readiness, MRP and the UI are built on top of it by other agents.

## Data

The spec is in `backend/app/plant.py`. The seed generator writes it into the warehouse, and `app/materials.py` reads it back from DuckDB/BigQuery.

| Table | Content |
|---|---|
| `dim_material` (extended) | 35 materials. New columns: `material_group`, `color`, `reorder_point`, `lead_time_days`, `default_vendor_id`, `default_warehouse_id` |
| `dim_warehouse` (new) | `WH-RM` resin silo, `WH-PNT` paint store, `WH-CMP` components and packaging |
| `bom_header` (new) | `bom_id` `BOM-2027-NNN`, `sku`, `version`, `status` Draft/Active/Inactive, `effective_date` |
| `bom_item` (new) | `bom_id`, `line_no`, `material_id`, `qty_per_unit`, `unit`, `scrap_pct`, `process`, `color` (null = every colour) |
| `fact_material_stock` (extended) | `warehouse_id`, `reserved_qty`, `incoming_qty`. Resin keeps its 90 daily rows; every other material has one row on the latest snapshot date |
| `fact_customer_order` (extended) | `color` on each order line |
| `bom` (legacy, unchanged shape) | Built from the Active BOMs. Colour items are averaged over the model's colours, and scrap is left out, as before. Resin and masterbatch quantities are unchanged |

**Materials.**
- Resin `PP-EPDM-T20` and masterbatch `MB-BLK`.
- Primer P1 (ARKA, BROMO) and P2 (CITRA).
- Basecoat `B-C01..B-C17`: 13 colours, one per colour in use.
- Clear K1/K2 with hardener H1/H2.
- Thinner by colour family: T1 solid, T2 metallic, T3 pearl. T4 is the clearcoat thinner.
- `BRK-<MODEL>-F|R`, `SNS-HLD`, `PKG-RT-<MODEL>`.

**Colour families** (`plant.COLOR_FAMILY`) decide which thinner a colour's BOM line uses. C07 is metallic, so it uses T2, as in the guide's example.

**Thresholds.**
- `safety_stock` = 2 days of planned use.
- `reorder_point` = safety stock + lead time × daily use.
- Daily use = `FEB_PLAN_PER_DAY` × Active BOM × scrap, with each colour's share taken from `plant.color_weights`, the same weights the stock snapshot uses.
- Resin keeps its fixed 12,000 kg safety stock, so the resin story does not change.

**BOMs.**
- Each SKU has one Active BOM, effective 2027-01-18.
- FB-ARKA also has an older Inactive v1.0 (`BOM-2027-001`) without the family thinners, to show versioning.
- `plant.bom_problems()` checks the BOMs: one Active per SKU, every material known, quantity > 0, a valid process and a known colour.

**Order colours in the seed.**
- Assigned with no RNG, so the planted stories keep their random stream.
- Each SKU's ordered quantity per colour follows the snapshot weights (a greedy deficit pick).

## Planted conditions (`PLANTED_STOCK`, `OPEN_NON_RESIN_POS` in the generator)

| Condition | Numbers |
|---|---|
| B-C07 is enough this week, short at +40% | On hand 615, reserved 25, so 590 kg usable. This week's ARKA C07 order lines (2027-02-01..06): FB 711 + RB 648 pcs × 0.3 kg × 1.3 scrap = **530 kg**. With FB-ARKA at +40%: **641 kg**. The open PO for 120 kg is promised 2027-02-08, after the week, so it does not help a need date inside the week |
| BRK-ARKA-F below reorder point | On hand 5,200, reserved 400: 4,800 < reorder point 5,970. Open PO for 3,500 sets, promised 2027-02-02, expected 2027-02-03 |
| Resin about 9 days out | Unchanged: 138,400 kg, no open resin PO |
| Incoming visible | Open POs: BRK-ARKA-F 3,500, B-C07 120, P1 2,400, K1 1,500 |

## Python API (`app/materials.py`)

These are the fixed signatures from the contract:
- `list_materials`
- `get_material` (FV-MAT-002)
- `list_warehouses`
- `model_colors`, `sku_colors`
- `list_boms`
- `active_bom` (FV-MAT-001; FV-MAT-007 if an item points at an unknown material)
- `bom_items_for`
- `color_split`
- `incoming`
- `stock`
- `transactions`
- `clear`

Additions:
- `get_bom` (FV-MAT-005)
- `check_color` (FV-MAT-003)
- `color_catalog`
- `inventory` (the HTTP payload)
- `record_transaction(sid, row)`: the canonical writer for the `material_transactions` ledger. Store key `f"{sid}:{txn_id}"`.
- `reset_cache`

Rules worth knowing:
- `bom_items_for(sku, None)` returns only the colourless items, and every returned item carries `basecoat_unresolved: True`. With a colour, every item carries `False`.
- `color_split` takes its shares from the latest `fact_stock_snapshot.hourly_use`, falling back to equal shares. A largest-remainder split makes the parts sum exactly to `round(qty)`.
- `incoming`:
  - `expected_date` = `promised_date` + round(vendor's average historical delay + the scenario's extra vendor delay).
  - POs approved in the session (`Overrides.extra_receipts`, any material) appear with `source: approved_po`.
- `stock` calculation:
  - `reserved_qty` = seed reserved + Active `material_reservations` docs of the session.
  - `available_qty` = on hand − reserved + all incoming (undated).
  - `below_reorder` = on hand − reserved < reorder point.
- `transactions`:
  - Resin rows come from `fact_inventory_move`, with a running `balance_after` that ends at the latest on hand.
  - Materials with no moves get one "Opening balance (stock take)" Adjust row.
  - Session rows follow. Reserve and Release rows keep the on-hand balance.
- `clear(sid)` deletes the session's ledger rows. `POST /api/demo/reset` calls it.

## HTTP (`app/routes/master.py`)

- `GET /api/master/materials?group=` → `{materials, groups}`. An unknown group returns FV-MAT-008.
- `GET /api/master/warehouses` → `{warehouses}`
- `GET /api/master/colors` → `{models, skus, colors:[{color, family, basecoat_id, thinner_id, models}]}`
- `GET /api/master/boms?sku=&status=` → `{boms, statuses, as_of}`. Each BOM is a header plus `colors`, `item_count` and `items` (with `material_name`, `material_group`). Errors: FV-MAT-006 for an unknown SKU, FV-MAT-009 for an unknown status.
- `GET /api/master/boms/{bom_id}`. Unknown id: FV-MAT-005.
- `GET /api/inventory?material_id=&warehouse_id=&group=` → `{rows, kpis:{materials, below_reorder, incoming_pos, value_idr}, formula, source, as_of}`. Errors: FV-MAT-002 for an unknown material, FV-MAT-004 for an unknown warehouse.
- `GET /api/inventory/transactions?material_id=` → `{transactions}`

## Order colour (MR-R04 backend)

- `OrderLineIn.color` is optional.
- `validate_order` adds a field error `lines.N.color` when the colour is not one of the SKU's colours.
  - `POST /api/orders` raises **FV-MAT-003** with the field list.
  - `/validate` returns the field error.
- A line with no colour gets a warning: its basecoat will be split by usage share.
- The duplicate-line rule is now per SKU × colour.
- `orders.products()` lists each SKU's `colors` for the wizard.
- Order lines, plan demand candidates, plan `demand` rows, demand-breakdown sources and draft demand rows all carry `color` (null allowed).

## Parity with the old repo

**Matched:**
- Header + item BOM.
- Version, defaulting to `v1.0`.
- Statuses Draft/Active/Inactive.
- One Active BOM per product, taking the Active one in date (`effective_date <= today`, latest first).
- `BOM-YYYY-NNN` numbering.
- Scrap % per item, and item line numbers.
- An unknown component is an error, never a silent zero.
- Inventory per material × warehouse with on hand, reserved, incoming, available, reorder point and safety stock, using Available = On Hand − Reserved + Incoming.
- Ledger with a balance after each row, and the transaction types receipt, issue, adjustment, reservation and release.

**Intentional differences:**
- Materials are their own table, not product rows.
- No multi-level sub-assemblies.
- A colour variant per BOM item (new).
- No BOM create/edit (MR-R08: master data comes from the seed).
- No manual adjust/receive (MR-R45).
- Incoming carries an expected date (promise + vendor delay), which readiness uses to decide what counts.
- The ledger has a signed `qty` instead of separate on-hand, reserved and incoming deltas.
- Non-resin materials have no historical moves, so their ledger starts at an opening stock-take row.
- `fact_material_stock` keeps its primary key `(snapshot_date, material_id)`. `warehouse_id` is a nullable column, because each material lives in one warehouse and an additive migration cannot change a primary key.

## Migration

`backend/migrations/versions/0003_material_master.py` (down_revision `0002`) adds:
- three tables: `dim_warehouse`, `bom_header`, `bom_item`
- nullable columns on `dim_material`, `fact_material_stock` and `fact_customer_order`

`downgrade` drops them again. **It needs human review before merge.**
