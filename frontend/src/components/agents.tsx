import { useState } from "react";
import { Link } from "react-router-dom";
import { errorText, post } from "../lib/api";
import { useApp } from "../lib/store";
import type { Action, TimelineItem } from "../lib/types";
import { Badge, Card, Markdown } from "./ui";

const AGENT_LABEL: Record<string, string> = {
  orchestrator: "Orchestrator",
  demand_forecast_agent: "Demand Forecast",
  production_planning_agent: "Production Planning",
  mes_agent: "MES",
  material_supply_agent: "Material Supply",
  purchasing_agent: "Purchasing",
  quality_control_agent: "Quality Control",
  delivery_agent: "Delivery",
};
export const agentLabel = (a?: string) => (a ? AGENT_LABEL[a] ?? a : "");
const MODULE_LABEL: Record<string, string> = { orders: "Customer Orders", forecast: "Demand Forecast", planning: "Production Planning", mes: "MES", material: "Material Supply", purchasing: "Purchasing", quality: "Quality Control", delivery: "Delivery", orchestrator: "Orchestrator" };
export const moduleLabel = (m: string) => MODULE_LABEL[m] ?? m;

/** Agent reasoning timeline (OR-4): which agent ran, which tool, what came back. */
export function Timeline({ items }: { items: TimelineItem[] }) {
  if (!items.length) return <p className="muted">Waiting for the first agent…</p>;
  return (
    <ol className="timeline">
      {items.map((it, i) => {
        if (it.kind === "agent_start") return <li key={i} className="tl-agent">▸ {agentLabel(it.agent)} agent</li>;
        if (it.kind === "agent_end") return null;
        if (it.kind === "tool_call")
          return (
            <li key={i} className="tl-call">
              <code>{it.tool}</code>
              <span className="muted"> {Object.entries(it.args ?? {}).map(([k, v]) => `${k}=${v}`).join(", ")}</span>
            </li>
          );
        return (
          <li key={i} className={`tl-result ${it.error ? "bad" : ""}`}>
            ↳ {it.summary}
          </li>
        );
      })}
    </ol>
  );
}

/** Side panel with the running or finished Disruption Response. */
export function RunPanel() {
  const { run, panelOpen, setPanelOpen } = useApp();
  if (!panelOpen) return null;
  return (
    <aside className="run-panel" aria-label="Disruption Response">
      <header className="card-head">
        <h2>Disruption Response</h2>
        <button className="ghost" onClick={() => setPanelOpen(false)} aria-label="Close panel">✕</button>
      </header>
      <p className="muted small">
        {run.source === "replay" ? "Replay of a recorded run: no Gemini calls." : "Live run with Gemini on Vertex AI."}
        {run.recordedWith && <> Recorded with: {run.recordedWith}.</>}
      </p>
      {run.status === "running" && <p className="pulse">Agents are working…</p>}
      {run.error && <p className="error" role="alert">{run.error}</p>}
      <Timeline items={run.timeline} />
      {run.status === "done" && (
        <div className="plan-text">
          <h3>Action plan</h3>
          <Markdown text={run.text} />
          <Link className="button primary" to="/actions" onClick={() => setPanelOpen(false)}>
            Review and approve in the Action Center
          </Link>
        </div>
      )}
    </aside>
  );
}

/** Ask one module agent (live mode only). */
export function AskAgent({ module, placeholder }: { module: string; placeholder: string }) {
  const { live, mode, bump } = useApp();
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState<{ text: string; timeline: TimelineItem[]; actions: Action[] } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const disabled = !live || !mode?.ai_enabled;
  const ask = async () => {
    setBusy(true);
    setErr(null);
    try {
      const r = await post<{ text: string; timeline: TimelineItem[]; actions: Action[] }>(`/api/agents/${module}/ask`, { question: q || placeholder });
      setAnswer(r);
      if (r.actions.length) bump();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Ask the agent">
      <div className="ask">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder} aria-label="Question for the agent" disabled={disabled || busy} />
        <button className="primary" onClick={ask} disabled={disabled || busy}>{busy ? "Thinking…" : "Ask"}</button>
      </div>
      {disabled && <p className="muted small">Switch to Live mode to ask the agent{mode && !mode.ai_enabled ? " (AI is not enabled on this server)" : ""}.</p>}
      {err && <p className="error" role="alert">{err}</p>}
      {answer && (
        <>
          <Markdown text={answer.text} />
          {answer.actions.length > 0 && (
            <p>
              <Badge tone="info">{answer.actions.length} proposal(s)</Badge> <Link to="/actions">Open the Action Center</Link>
            </p>
          )}
          <details>
            <summary>Agent reasoning ({answer.timeline.filter((t) => t.kind === "tool_call").length} tool calls)</summary>
            <Timeline items={answer.timeline} />
          </details>
        </>
      )}
    </Card>
  );
}
