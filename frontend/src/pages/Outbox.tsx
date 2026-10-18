import { useState } from "react";
import { moduleLabel } from "../components/agents";
import { Badge, Card, Status } from "../components/ui";
import { errorText, getBlob, post } from "../lib/api";
import { useApp, useData } from "../lib/store";

export type Email = {
  email_id: string;
  kind: string;
  origin_agent: string;
  plan_id: string | null;
  created_at: string;
  status: "queued" | "draft" | "sent" | "not_sent" | "failed";
  reason: string | null;
  to: string[];
  cc: string[];
  subject: string;
  body: string;
  sent_at?: string;
};

const TONE = { sent: "good", draft: "info", queued: "neutral", not_sent: "warn", failed: "bad" } as const;
const KIND: Record<string, string> = {
  purchase_order: "PO to vendor",
  expedite_po: "Expedite request",
  vendor_reply: "Reply to vendor",
  customer_notice: "Customer notice",
  ship_date: "Ship date change",
  shortage_alert: "Shortage alert",
  maintenance: "Maintenance work request",
  quality_hold: "Hold notice",
  plan_summary: "Approved plan summary",
  shift_briefing: "Shift briefing",
};

function EmailCard({ e, onSend, busy }: { e: Email; onSend: (id: string) => void; busy: boolean }) {
  const download = async () => {
    const url = URL.createObjectURL(await getBlob(`/api/outbox/${e.email_id}/eml`));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${e.email_id}.eml`;
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <article className="action">
      <header>
        <h3>{e.subject}</h3>
        <Badge tone={TONE[e.status]}>{e.status.replace("_", " ")}</Badge>
      </header>
      <p className="muted small">
        {KIND[e.kind] ?? e.kind} · written by the {moduleLabel(e.origin_agent)} agent · to {e.to.join(", ")}
        {e.cc.length > 0 && <> · cc {e.cc.join(", ")}</>}
      </p>
      <details>
        <summary>Email</summary>
        <pre className="notice">{e.body}</pre>
      </details>
      {e.reason && <p className="muted small">{e.reason}</p>}
      <div className="row">
        {e.status !== "sent" && <button className={e.status === "draft" ? "primary" : ""} disabled={busy} onClick={() => onSend(e.email_id)}>Send</button>}
        <button className="ghost" onClick={download}>Download .eml</button>
      </div>
    </article>
  );
}

export default function OutboxPage() {
  const { data, error, loading } = useData<{ emails: Email[]; sent: number; configured: boolean }>("/api/outbox");
  const { bump } = useApp();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const act = async (path: string) => {
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
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Outbox</h1>
          <p className="muted">
            Emails the agents write after a person approves an action: POs to vendors, notices to customers, work requests, alerts and management summaries.
            {data && (data.configured ? " Sent through Gmail to the demo inbox." : " Gmail is not configured on this server, so emails stay here.")}
          </p>
        </div>
        <button className="primary" disabled={busy} onClick={() => act("/api/outbox/briefing")}>Email the shift briefing</button>
      </div>
      <Status loading={loading} error={error} />
      {err && <p className="error" role="alert">{err}</p>}
      {data && !data.emails.length && (
        <Card><p className="muted">No emails yet. Approve a proposal in the Action Center, or press Check now on a plan in Material Supply.</p></Card>
      )}
      {data && data.emails.length > 0 && (
        <Card title={`${data.emails.length} email(s), ${data.sent} sent`}>
          <div className="actions-grid">
            {data.emails.map((e) => <EmailCard key={e.email_id} e={e} busy={busy} onSend={(id) => act(`/api/outbox/${id}/send`)} />)}
          </div>
        </Card>
      )}
    </>
  );
}
