import { useState } from "react";
import { moduleLabel } from "../components/agents";
import { Badge, Card, ImpactRow, Status } from "../components/ui";
import { errorText, post } from "../lib/api";
import { useApp, useData } from "../lib/store";
import type { Action, Impact } from "../lib/types";

const TONE = { pending: "warn", approved: "good", rejected: "neutral", failed: "bad" } as const;

function total(items: Action[]): Impact {
  const sum = (k: keyof Impact) => items.reduce((a, x) => a + (x.impact[k] ?? 0), 0);
  return { rupiah_idr: sum("rupiah_idr"), scrap_kg: sum("scrap_kg"), kwh: sum("kwh"), co2e_kg: sum("co2e_kg") };
}

function ActionCard({ a, onDecide, busy }: { a: Action; onDecide: (id: string, approve: boolean) => void; busy: boolean }) {
  const notice = a.kind === "customer_notice" ? String(a.command.args.message ?? "") : null;
  return (
    <article className={`action ${a.status}`}>
      <header>
        <h3>{a.title}</h3>
        <Badge tone={TONE[a.status]}>{a.status}</Badge>
      </header>
      <p className="muted small">
        Proposed by the {moduleLabel(a.origin_agent)} agent
        {a.decided_by && <> · {a.status} by {a.decided_by} at {a.decided_at?.slice(11, 16)} UTC</>}
      </p>
      {notice ? <pre className="notice">{notice}</pre> : <p>{a.rationale}</p>}
      <ImpactRow impact={a.impact} />
      {a.evidence.length > 0 && (
        <details>
          <summary>Evidence ({a.evidence.length})</summary>
          <ul className="small">{a.evidence.map((e, i) => <li key={i}>{e.source}</li>)}</ul>
        </details>
      )}
      {a.execution && !a.execution.ok && <p className="error">Execution failed: {JSON.stringify(a.execution.error)}</p>}
      {a.status === "pending" && (
        <div className="row">
          <button className="primary" disabled={busy} onClick={() => onDecide(a.action_id, true)}>Approve</button>
          <button disabled={busy} onClick={() => onDecide(a.action_id, false)}>Reject</button>
        </div>
      )}
    </article>
  );
}

export default function ActionCenter() {
  const { data, error, loading } = useData<{ actions: Action[]; plans: Record<string, string[]> }>("/api/actions");
  const { bump } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const decide = async (path: string) => {
    setBusy(true);
    setErr(null);
    try {
      await post(path);
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
      bump();
    }
  };
  const groups = new Map<string, Action[]>();
  for (const a of data?.actions ?? []) {
    const k = a.plan_id ?? "single";
    groups.set(k, [...(groups.get(k) ?? []), a]);
  }
  return (
    <>
      <h1>Action Center</h1>
      <p className="muted">Every agent action is a proposal. Nothing reaches the plant, the twin or a customer until a person approves it.</p>
      <Status loading={loading} error={error} />
      {err && <p className="error" role="alert">{err}</p>}
      {data && !data.actions.length && <Card><p className="muted">No proposals yet. Run the Disruption Response from the Plant Briefing.</p></Card>}
      {[...groups.entries()].reverse().map(([plan, items]) => {
        const pending = items.filter((a) => a.status === "pending").length;
        return (
          <Card
            key={plan}
            title={plan.startsWith("PLAN") ? `Cross-module plan ${plan}` : plan.startsWith("ASK") ? `Agent question ${plan}` : "Proposals"}
            actions={
              pending > 0 && plan !== "single" ? (
                <div className="row">
                  <button className="primary big" disabled={busy} onClick={() => decide(`/api/plans/${plan}/approve`)}>Approve plan ({pending})</button>
                  <button disabled={busy} onClick={() => decide(`/api/plans/${plan}/reject`)}>Reject all</button>
                </div>
              ) : undefined
            }
          >
            <div className="plan-total">
              <span className="muted">Plan impact (impact_calculator, per month):</span> <ImpactRow impact={total(items)} />
            </div>
            <div className="actions-grid">
              {items.map((a) => (
                <ActionCard key={a.action_id} a={a} busy={busy} onDecide={(id, ok) => decide(`/api/actions/${id}/${ok ? "approve" : "reject"}`)} />
              ))}
            </div>
          </Card>
        );
      })}
    </>
  );
}
