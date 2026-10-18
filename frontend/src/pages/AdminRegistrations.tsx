import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Table } from "../components/ui";
import { errorText, get, post } from "../lib/api";
import { useAuth, type Account } from "../lib/auth";

type Registrations = { pending: Account[]; decided: Account[] };

export default function AdminRegistrationsPage() {
  const { user, account } = useAuth();
  const [data, setData] = useState<Registrations | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      setData(await get<Registrations>("/api/admin/registrations"));
      setErr(null);
    } catch (e) {
      setErr(errorText(e));
    }
  }, []);
  useEffect(() => {
    if (account?.is_admin) void load();
  }, [account?.is_admin, load]);
  const decide = async (uid: string, ok: boolean) => {
    const note = ok ? undefined : window.prompt("Reason for the rejection (sent to the applicant)") ?? undefined;
    setBusy(true);
    try {
      await post(`/api/admin/registrations/${encodeURIComponent(uid)}/${ok ? "approve" : "reject"}`, { note });
      await load();
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  if (!user || !account?.is_admin) {
    return (
      <>
        <h1>Registrations</h1>
        <Card><p>Admins only. <Link to="/account">Sign in</Link> with an admin Google account.</p></Card>
      </>
    );
  }
  return (
    <>
      <h1>Registrations</h1>
      <p className="muted">People who signed up with Google. Approving opens onboarding for them and emails them; rejecting emails the reason.</p>
      {err && <p className="error" role="alert">{err}</p>}
      <Card title={`Waiting for approval (${data?.pending.length ?? 0})`}>
        <Table
          cols={[
            { key: "who", label: "Applicant" },
            { key: "factory", label: "Factory" },
            { key: "role", label: "Role" },
            { key: "when", label: "Registered" },
            { key: "act", label: "" },
          ]}
          rows={(data?.pending ?? []).map((a) => ({
            who: <>{a.name}<div className="muted small">{a.email}</div></>,
            factory: <>{a.factory_name}<div className="muted small">{a.industry}{a.city ? ` · ${a.city}` : ""}{a.plant_scale ? ` · ${a.plant_scale}` : ""}</div></>,
            role: a.role,
            when: a.created_at?.slice(0, 16).replace("T", " "),
            act: (
              <span className="row">
                <button className="primary" disabled={busy} onClick={() => a.uid && decide(a.uid, true)}>Approve</button>
                <button disabled={busy} onClick={() => a.uid && decide(a.uid, false)}>Reject</button>
              </span>
            ),
          }))}
          empty="No registration waits for approval."
        />
      </Card>
      <Card title="Decided">
        <Table
          cols={[
            { key: "who", label: "Applicant" },
            { key: "factory", label: "Factory" },
            { key: "status", label: "Status" },
            { key: "by", label: "Decided by" },
            { key: "onb", label: "Onboarding" },
          ]}
          rows={(data?.decided ?? []).map((a) => ({
            who: <>{a.name}<div className="muted small">{a.email}</div></>,
            factory: a.factory_name,
            status: <Badge tone={a.status === "approved" ? "good" : "neutral"}>{a.status}</Badge>,
            by: a.decided_by,
            onb: a.onboarding ? `${a.onboarding.done}/${a.onboarding.total}` : "",
          }))}
          empty="Nothing decided yet."
        />
      </Card>
    </>
  );
}
