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
