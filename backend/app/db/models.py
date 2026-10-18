from datetime import date, datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Double, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeEngine

SCHEMA = "fv"


class Base(DeclarativeBase):
    metadata = MetaData()


Str = String
Int = BigInteger
Ts = DateTime(timezone=True)
Dbl = Double


def pk(type_: type[TypeEngine[Any]] | TypeEngine[Any] = Str) -> Mapped[Any]:
    return mapped_column(type_, primary_key=True, autoincrement=False)


def col(type_: type[TypeEngine[Any]] | TypeEngine[Any] = Str) -> Mapped[Any]:
    return mapped_column(type_, nullable=True)


class DimMachine(Base):
    __tablename__ = "dim_machine"
    machine_id: Mapped[str] = pk()
    machine_name: Mapped[str | None] = col()
    process: Mapped[str | None] = col()
    area: Mapped[str | None] = col()
    tonnage: Mapped[int | None] = col(Int)
    kw_rated: Mapped[float | None] = col(Dbl)
    cycle_alarm_s: Mapped[float | None] = col(Dbl)


class DimProduct(Base):
    __tablename__ = "dim_product"
    sku: Mapped[str] = pk()
    model: Mapped[str | None] = col()
    segment: Mapped[str | None] = col()
    position: Mapped[str | None] = col()
    ideal_cycle_s: Mapped[float | None] = col(Dbl)
    shot_weight_kg: Mapped[float | None] = col(Dbl)
    price_idr: Mapped[int | None] = col(Int)
    colors: Mapped[str | None] = col()


class DimMaterial(Base):
    __tablename__ = "dim_material"
    material_id: Mapped[str] = pk()
    material_name: Mapped[str | None] = col()
    material_type: Mapped[str | None] = col()
    unit: Mapped[str | None] = col()
    unit_cost_idr: Mapped[int | None] = col(Int)
    safety_stock: Mapped[float | None] = col(Dbl)
    material_group: Mapped[str | None] = col()
    color: Mapped[str | None] = col()
    reorder_point: Mapped[float | None] = col(Dbl)
    lead_time_days: Mapped[int | None] = col(Int)
    default_vendor_id: Mapped[str | None] = col()
    default_warehouse_id: Mapped[str | None] = col()


class DimWarehouse(Base):
    __tablename__ = "dim_warehouse"
    warehouse_id: Mapped[str] = pk()
    warehouse_name: Mapped[str | None] = col()
    warehouse_type: Mapped[str | None] = col()


class BomHeader(Base):
    __tablename__ = "bom_header"
    bom_id: Mapped[str] = pk()
    sku: Mapped[str | None] = col()
    version: Mapped[str | None] = col()
    status: Mapped[str | None] = col()
    effective_date: Mapped[date | None] = col(Date)


class BomItem(Base):
    __tablename__ = "bom_item"
    item_id: Mapped[str] = pk()
    bom_id: Mapped[str | None] = col()
    line_no: Mapped[int | None] = col(Int)
    material_id: Mapped[str | None] = col()
    qty_per_unit: Mapped[float | None] = col(Dbl)
    unit: Mapped[str | None] = col()
    scrap_pct: Mapped[float | None] = col(Dbl)
    process: Mapped[str | None] = col()
    color: Mapped[str | None] = col()


class DimMold(Base):
    __tablename__ = "dim_mold"
    mold_id: Mapped[str] = pk()
    sku: Mapped[str | None] = col()
    cavity: Mapped[int | None] = col(Int)
    current_machine_id: Mapped[str | None] = col()


class ProductMoldCompatibility(Base):
    __tablename__ = "product_mold_compatibility"
    sku: Mapped[str] = pk()
    mold_id: Mapped[str | None] = col()
    machine_id: Mapped[str] = pk()


class Bom(Base):
    __tablename__ = "bom"
    sku: Mapped[str] = pk()
    material_id: Mapped[str] = pk()
    qty_per_unit: Mapped[float | None] = col(Dbl)
    unit: Mapped[str | None] = col()


class Routing(Base):
    __tablename__ = "routing"
    seq: Mapped[int] = pk(Int)
    process: Mapped[str | None] = col()
    machine_id: Mapped[str | None] = col()


class DimCustomer(Base):
    __tablename__ = "dim_customer"
    customer_id: Mapped[str] = pk()
    customer_name: Mapped[str | None] = col()
    customer_type: Mapped[str | None] = col()
    penalty_idr_per_day: Mapped[int | None] = col(Int)
    pic_name: Mapped[str | None] = col()
    pic_contact: Mapped[str | None] = col()
    delivery_address: Mapped[str | None] = col()
    dock_number: Mapped[str | None] = col()


class DimVendor(Base):
    __tablename__ = "dim_vendor"
    vendor_id: Mapped[str] = pk()
    vendor_name: Mapped[str | None] = col()
    category: Mapped[str | None] = col()
    lead_time_days: Mapped[int | None] = col(Int)
    unit_price_idr: Mapped[int | None] = col(Int)


class DimShift(Base):
    __tablename__ = "dim_shift"
    shift_id: Mapped[str] = pk()
    start_hour: Mapped[int | None] = col(Int)
    end_hour: Mapped[int | None] = col(Int)
    planned_minutes: Mapped[int | None] = col(Int)


class DimDefect(Base):
    __tablename__ = "dim_defect"
    defect_code: Mapped[str] = pk()
    shop_term: Mapped[str | None] = col()
    defect_name: Mapped[str | None] = col()
    ng_group: Mapped[str | None] = col()
    defect_category: Mapped[str | None] = col()
    default_disposition: Mapped[str | None] = col()


class DimDowntimeReason(Base):
    __tablename__ = "dim_downtime_reason"
    reason_code: Mapped[str] = pk()
    reason_name: Mapped[str | None] = col()
    loss_category: Mapped[str | None] = col()
    reason_category: Mapped[str | None] = col()
    shop_term: Mapped[str | None] = col()


class FactProduction(Base):
    __tablename__ = "fact_production"
    production_date: Mapped[date] = pk(Date)
    shift_id: Mapped[str] = pk()
    machine_id: Mapped[str] = pk()
    sku: Mapped[str | None] = col()
    resin_lot_id: Mapped[str | None] = col()
    planned_minutes: Mapped[float | None] = col(Dbl)
    run_minutes: Mapped[float | None] = col(Dbl)
    downtime_minutes: Mapped[float | None] = col(Dbl)
    ideal_cycle_s: Mapped[float | None] = col(Dbl)
    actual_cycle_s: Mapped[float | None] = col(Dbl)
    total_quantity: Mapped[int | None] = col(Int)
    output_quantity: Mapped[int | None] = col(Int)
    reject_quantity: Mapped[int | None] = col(Int)
    scrap_quantity: Mapped[int | None] = col(Int)
    rework_quantity: Mapped[int | None] = col(Int)
    resin_kg: Mapped[float | None] = col(Dbl)


class FactDowntime(Base):
    __tablename__ = "fact_downtime"
    downtime_id: Mapped[str] = pk()
    production_date: Mapped[date | None] = col(Date)
    shift_id: Mapped[str | None] = col()
    machine_id: Mapped[str | None] = col()
    start_ts: Mapped[datetime | None] = col(Ts)
    end_ts: Mapped[datetime | None] = col(Ts)
    duration_minutes: Mapped[float | None] = col(Dbl)
    reason_code: Mapped[str | None] = col()
    machine_status: Mapped[str | None] = col()


class FactInspection(Base):
    __tablename__ = "fact_inspection"
    inspection_date: Mapped[date] = pk(Date)
    shift_id: Mapped[str] = pk()
    sku: Mapped[str] = pk()
    resin_lot_id: Mapped[str | None] = col()
    humidity_pct: Mapped[float | None] = col(Dbl)
    inspected_qty: Mapped[int | None] = col(Int)
    ok_qty: Mapped[int | None] = col(Int)


class FactQuality(Base):
    __tablename__ = "fact_quality"
    inspection_date: Mapped[date] = pk(Date)
    shift_id: Mapped[str] = pk()
    sku: Mapped[str] = pk()
    resin_lot_id: Mapped[str | None] = col()
    humidity_pct: Mapped[float | None] = col(Dbl)
    defect_code: Mapped[str] = pk()
    defect_qty: Mapped[int | None] = col(Int)
    disposition: Mapped[str | None] = col()


class FactInventoryMove(Base):
    __tablename__ = "fact_inventory_move"
    move_id: Mapped[str] = pk()
    move_ts: Mapped[datetime | None] = col(Ts)
    move_date: Mapped[date | None] = col(Date)
    material_id: Mapped[str | None] = col()
    lot_id: Mapped[str | None] = col()
    move_type: Mapped[str | None] = col()
    quantity: Mapped[float | None] = col(Dbl)
    reference_id: Mapped[str | None] = col()


class FactMaterialStock(Base):
    __tablename__ = "fact_material_stock"
    snapshot_date: Mapped[date] = pk(Date)
    material_id: Mapped[str] = pk()
    warehouse_id: Mapped[str | None] = col()
    on_hand_qty: Mapped[float | None] = col(Dbl)
    reserved_qty: Mapped[float | None] = col(Dbl)
    incoming_qty: Mapped[float | None] = col(Dbl)


class FactCustomerOrder(Base):
    __tablename__ = "fact_customer_order"
    order_id: Mapped[str] = pk()
    customer_id: Mapped[str | None] = col()
    sku: Mapped[str | None] = col()
    color: Mapped[str | None] = col()
    order_qty: Mapped[int | None] = col(Int)
    order_date: Mapped[date | None] = col(Date)
    requested_delivery_date: Mapped[date | None] = col(Date)
    order_status: Mapped[str | None] = col()
    shipped_qty: Mapped[int | None] = col(Int)
    # Header fields, repeated on every line of the order (one row = one order line).
    order_number: Mapped[str | None] = col()
    line_no: Mapped[int | None] = col(Int)
    po_number: Mapped[str | None] = col()
    order_channel: Mapped[str | None] = col()
    customer_pic: Mapped[str | None] = col()
    delivery_address: Mapped[str | None] = col()
    dock_number: Mapped[str | None] = col()


class FactMonthlyPlan(Base):
    __tablename__ = "fact_monthly_plan"
    plan_month: Mapped[date] = pk(Date)
    sku: Mapped[str] = pk()
    plan_qty: Mapped[int | None] = col(Int)
    source: Mapped[str | None] = col()


class FactStockSnapshot(Base):
    __tablename__ = "fact_stock_snapshot"
    snapshot_ts: Mapped[datetime] = pk(Ts)
    sku: Mapped[str] = pk()
    color: Mapped[str] = pk()
    wip_qty: Mapped[int | None] = col(Int)
    mezzanine_qty: Mapped[int | None] = col(Int)
    sgl_qty: Mapped[int | None] = col(Int)
    hourly_use: Mapped[float | None] = col(Dbl)
    target_hours: Mapped[float | None] = col(Dbl)
    resin_lot_id: Mapped[str | None] = col()


class FactPurchaseOrder(Base):
    __tablename__ = "fact_purchase_order"
    po_id: Mapped[str] = pk()
    vendor_id: Mapped[str | None] = col()
    material_id: Mapped[str | None] = col()
    order_qty: Mapped[float | None] = col(Dbl)
    unit_price_idr: Mapped[int | None] = col(Int)
    order_date: Mapped[date | None] = col(Date)
    promised_date: Mapped[date | None] = col(Date)
    received_date: Mapped[date | None] = col(Date)
    po_status: Mapped[str | None] = col()
    received_qty: Mapped[float | None] = col(Dbl)
    lot_id: Mapped[str | None] = col()


class FactShipment(Base):
    __tablename__ = "fact_shipment"
    shipment_id: Mapped[str] = pk()
    order_id: Mapped[str | None] = col()
    customer_id: Mapped[str | None] = col()
    sku: Mapped[str | None] = col()
    shipped_qty: Mapped[int | None] = col(Int)
    ship_date: Mapped[date | None] = col(Date)
    on_time: Mapped[bool | None] = col(Boolean)


class FactEnergy(Base):
    __tablename__ = "fact_energy"
    energy_date: Mapped[date] = pk(Date)
    machine_id: Mapped[str] = pk()
    kwh: Mapped[float | None] = col(Dbl)
    gas_m3: Mapped[float | None] = col(Dbl)
    co2e_kg: Mapped[float | None] = col(Dbl)


class PlantEvent(Base):
    __tablename__ = "plant_events"
    event_id: Mapped[str] = pk()
    session_id: Mapped[str | None] = col()
    event_ts: Mapped[datetime | None] = col(Ts)
    sim_ts: Mapped[datetime | None] = col(Ts)
    event_type: Mapped[str | None] = col()
    subject: Mapped[str | None] = col()
    payload: Mapped[str | None] = col()


PUBSUB_WRITTEN_TABLES = frozenset({"plant_events"})


def bq_type(sa_type: TypeEngine[Any]) -> str:
    if isinstance(sa_type, Boolean):
        return "BOOL"
    if isinstance(sa_type, BigInteger):
        return "INT64"
    if isinstance(sa_type, Double):
        return "FLOAT64"
    if isinstance(sa_type, DateTime):
        return "TIMESTAMP"
    if isinstance(sa_type, Date):
        return "DATE"
    return "STRING"


TABLES: dict[str, list[tuple[str, str]]] = {t.name: [(c.name, bq_type(c.type)) for c in t.columns] for t in Base.metadata.sorted_tables}
