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
