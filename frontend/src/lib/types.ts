export type Impact = { rupiah_idr: number | null; scrap_kg: number | null; kwh: number | null; co2e_kg: number | null };

export type Action = {
  action_id: string;
  plan_id: string | null;
  kind: string;
  module: string;
  title: string;
  origin_agent: string;
  rationale: string;
  evidence: { source: string }[];
  impact: Impact;
  command: { name: string; args: Record<string, unknown> };
  status: "pending" | "approved" | "rejected" | "failed";
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
  execution: { ok: boolean; result?: unknown; error?: unknown; executed_at: string } | null;
};

export type TimelineItem = {
  t_ms: number;
  kind: "agent_start" | "agent_end" | "tool_call" | "tool_result" | string;
  agent?: string;
  tool?: string;
  args?: Record<string, string>;
  summary?: string;
  error?: boolean;
};

export type Recording = {
  scenario: string;
  recorded_with: string;
  scripted: boolean;
  timeline: TimelineItem[];
  text: string;
  plan_id?: string;
  proposals: unknown[];
};

export type Machine = {
  machine_id: string;
  area: string;
  status: string;
  reason_code: string | null;
  andon: "green" | "yellow" | "red" | "blue" | "grey";
  cycle_s: number | null;
  ideal_cycle_s: number | null;
  performance_pct: number | null;
  mix: Record<string, number>;
  timer_min: number | null;
};

export type ConveyorItem = { seq: number; photo_id: string | null; sku: string; color: string; expected_color: string; defect_code: string | null; position: number };

export type Snapshot = {
  session_id: string;
  scenario: string;
  sim_minute: number;
  sim_ts: string;
  machines: Machine[];
  wip: Record<string, number>;
  warehouse: Record<string, { lot: string; units: number; on_hold: boolean }[]>;
  resin: { on_hand_kg: number; active_lot: string; incoming: { at_minute: number; qty_kg: number; lot: string }[] };
  holds: string[];
  output_today: Record<string, number>;
  conveyor: ConveyorItem[];
  state_hash: string;
  camera_hashes: Record<string, string>;
  events: { event_type: string; subject: string; sim_ts: string; payload: Record<string, unknown> }[];
};

export type Observation = { type: string; subject: string; value: number | null; severity: "low" | "medium" | "high"; confidence: number };

export type Mode = {
  ai_enabled: boolean;
  replays: string[];
  scenarios: string[];
  models: Record<string, string>;
  caps: Record<string, number>;
};

/* Production plan (CONTRACT.md "Production plan"); hand-mirrored from the plan API. */

export type PlanStatus = "Draft" | "Planning" | "Ready" | "Confirmed" | "In Execution" | "Completed" | "Cancelled";
export type CapacityStatus = "Within Plan" | "Additional Demand" | "Capacity Up Required";

export type Contribution = { machine_id: string; available_minutes: number; cycle_s: number; capacity: number };
export type Uncomputed = { machine_id: string; reason: string };

export type LineCapacity = {
  total: number;
  planning: number;
  buffer: number;
  gap: number;
  utilization_pct: number;
  status: CapacityStatus;
  contributions: Contribution[];
  uncomputed: Uncomputed[];
};

export type PlanLine = {
  line_id: string;
  sku: string;
  product_name: string;
  demand_qty: number;
  forecast_qty: number;
  forecast_line_ids: string[];
  planned_qty: number;
  priority: number;
  required_date: string | null;
  status: "Open" | "Released";
  capacity: LineCapacity | null;
};

export type PlanDemand = {
  order_id: string;
  order_number: string;
  line_no: number;
  customer_id: string;
  customer_name: string;
  sku: string;
  /** MR-R04: null for demand with no colour (old orders); material readiness splits it by usage share. */
  color?: string | null;
  qty: number;
  requested_delivery_date: string | null;
  line_id: string;
};

export type WorkOrderStatus = "Draft" | "Confirmed" | "Released" | "In Production" | "Completed";

export type PlanWorkOrder = {
  wo_id: string;
  plan_id: string;
  line_id: string;
  sku: string;
  process: "Molding" | "Painting" | "Assembly" | string;
  sequence: number | null;
  predecessor_wo_id: string | null;
  qty: number;
  machine_id: string | null;
  mold_id: string | null;
  planned_start: string | null;
  planned_end: string | null;
  shift: string | null;
  status: WorkOrderStatus;
  confirmed: boolean;
  /** Optional: the backend may list the compatible choices; the UI falls back to the plan's capacity contributions. */
  compatible_machines?: string[];
  compatible_molds?: string[];
};

export type ReadinessStep = { step: number; name: string; ready: boolean; blocked_by: string | null };

export type PlanHeader = {
  plan_id: string;
  plan_number: string;
  period_start: string;
  period_end: string;
  status: PlanStatus;
  utilization_pct: number;
  forecast_id: string | null;
  created_at: string;
  created_by: string;
  confirmed_at?: string | null;
  confirmed_by?: string | null;
  cancelled_at?: string | null;
  cancelled_by?: string | null;
  cancel_reason?: string | null;
  wizard_step: number;
  origin: "user" | "agent";
  /** Set on agent plans: "draft" from a period draft, absent for a re-plan. */
  agent_kind?: "draft" | null;
  approved_by?: string | null;
  draft_notes?: string[];
};

export type PlanListRow = PlanHeader & {
  capacity_status: CapacityStatus | null;
  line_count: number;
  total_planned: number;
  age_days: number;
  ageing: boolean;
};

export type PlanDetail = PlanHeader & {
  lines: PlanLine[];
  demand: PlanDemand[];
  readiness: { steps: ReadinessStep[]; resume_step: number };
  work_orders: PlanWorkOrder[];
  summary: { lines: number; total_planned: number; work_orders: number; customer_orders: number };
};

export type DemandCandidate = {
  order_id: string;
  order_number: string;
  line_no: number;
  customer_id: string;
  customer_name: string;
  sku: string;
  ordered_qty: number;
  planned_qty: number;
  remaining_qty: number;
  requested_delivery_date: string | null;
};

/* Material readiness, MRP, inventory and BOM (CONTRACT-MR.md); hand-mirrored from the material API. */

export type ReadinessStatus = "READY" | "PARTIAL" | "SHORTAGE" | "NOT_CHECKED";

export type ReadinessIncoming = { po_id: string; qty: number; expected_date: string; counted: boolean };

export type MaterialCheckRow = {
  material_id: string;
  material_name: string;
  material_group: string;
  color: string | null;
  unit: string;
  required_qty: number;
  required_date: string | null;
  on_hand_qty: number;
  reserved_qty: number;
  incoming_qty: number;
  incoming_counted_qty: number;
  incoming: ReadinessIncoming[];
  available_qty: number;
  shortage_qty: number;
  status: Exclude<ReadinessStatus, "NOT_CHECKED">;
  at_risk: boolean;
  note: string | null;
};

export type MaterialCheck = {
  plan_id: string;
  plan_number: string;
  wo_id: string | null;
  status: ReadinessStatus;
  readiness_pct: number | null;
  required_date: string | null;
  checked_at: string | null;
  materials: MaterialCheckRow[];
  shortages: MaterialCheckRow[];
};

export type ReadinessPlanRow = {
  plan_id: string;
  plan_number: string;
  period_start: string;
  period_end: string;
  status: ReadinessStatus;
  readiness_pct: number | null;
  short_count: number;
  checked_at: string | null;
  origin: string;
};

export type ReadinessOverview = {
  from: string;
  to: string;
  kpis: { readiness_pct: number | null; plans_monitored: number; plans_short: number; materials_short: number };
  plans: ReadinessPlanRow[];
};

export type MrpResult = {
  material_id: string;
  material_name: string;
  color: string | null;
  unit: string;
  gross_qty: number;
  available_qty: number;
  net_qty: number;
  required_date: string | null;
  order_by_date: string | null;
  lead_time_days: number | null;
  default_vendor_id: string | null;
  recommendation: string | null;
};

export type MrpRun = {
  run_id: string;
  horizon_start: string;
  horizon_end: string;
  plan_ids: string[];
  created_at: string;
  created_by: string;
  total_materials: number;
  shortage_materials: number;
  results: MrpResult[];
};

export type IncomingReceipt = {
  po_id: string;
  material_id: string;
  vendor_id: string | null;
  qty: number;
  promised_date: string | null;
  expected_date: string | null;
  avg_delay_days: number | null;
  source: "open_po" | "approved_po" | string;
};

export type StockRow = {
  material_id: string;
  material_name: string;
  material_group: string;
  color: string | null;
  unit: string;
  warehouse_id: string;
  on_hand_qty: number;
  reserved_qty: number;
  incoming_qty: number;
  incoming: IncomingReceipt[];
  available_qty: number;
  reorder_point: number | null;
  safety_stock: number | null;
  below_reorder: boolean;
};

export type InventoryView = { rows: StockRow[]; kpis: { materials: number; below_reorder: number; incoming_pos: number; value_idr: number } };

export type Reservation = {
  reservation_id: string;
  plan_id: string;
  wo_id: string;
  material_id: string;
  warehouse_id: string | null;
  qty: number;
  status: "Active" | "Released";
  created_at: string;
  released_at: string | null;
  unit?: string;
  material_name?: string;
};

export type ConsumptionRow = {
  wo_id?: string | null;
  material_id: string;
  material_name?: string;
  unit?: string;
  planned_qty: number;
  actual_qty: number;
  variance_qty: number;
  variance_pct: number | null;
  status: "NORMAL" | "OVER_CONSUMPTION" | "UNDER_CONSUMPTION" | string;
};

export type LedgerRow = {
  txn_id: string;
  date: string;
  material_id: string;
  warehouse_id: string | null;
  type: "Receipt" | "Issue" | "Adjust" | "Reserve" | "Release" | string;
  qty: number;
  balance_after: number;
  ref: string | null;
};

export type BomItem = {
  bom_id: string;
  material_id: string;
  material_name: string;
  material_group: string;
  qty_per_unit: number;
  unit: string;
  scrap_pct: number;
  process: "Molding" | "Painting" | "Assembly" | "Packing" | string;
  color: string | null;
};

export type Bom = { bom_id: string; sku: string; version: number | string; status: "Draft" | "Active" | "Inactive"; effective_date: string | null; items: BomItem[] };

export type Warehouse = { warehouse_id: string; name?: string; warehouse_name?: string };
