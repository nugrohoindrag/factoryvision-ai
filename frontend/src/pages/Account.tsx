import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, Field } from "../components/ui";
import { errorText, get, post } from "../lib/api";
import { useAuth, type Account } from "../lib/auth";
import { useData } from "../lib/store";

type Template = { template_id: string; name: string; industry: string; summary: string; master_data: string[] };

function SignIn() {
  const { enabled, ready, signIn, error } = useAuth();
  return (
    <Card title="Sign in">
      <p>Sign up with your Google account to get your own Factory Vision AI workspace. An admin approves each registration; then the onboarding guide loads a factory template for you.</p>
      <div className="row">
        <button className="primary big" onClick={signIn} disabled={!enabled}>Sign in with Google</button>
        <Link to="/">Continue as guest (demo, Replay)</Link>
      </div>
      {ready && !enabled && <p className="muted small">Sign-in is not configured on this server; the demo stays open to guests.</p>}
      {error && <p className="error small" role="alert">{error}</p>}
    </Card>
  );
}

function Register({ account }: { account: Account }) {
  const { setAccount } = useAuth();
  const [form, setForm] = useState({ full_name: account.name ?? "", factory_name: "", industry: "automotive", role: "Plant manager", city: "", plant_scale: "" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      setAccount(await post<Account>("/api/account/register", form));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Register your factory">
      <p className="muted">Signed in as {account.email}. Tell us about your factory; an admin reviews the registration.</p>
      <div className="form-grid">
        <Field label="Full name"><input value={form.full_name} onChange={set("full_name")} /></Field>
        <Field label="Company / factory name"><input value={form.factory_name} onChange={set("factory_name")} placeholder="PT Maju Presisi" /></Field>
        <Field label="Industry">
          <select value={form.industry} onChange={set("industry")}>{(account.industries ?? []).map((i) => <option key={i}>{i}</option>)}</select>
        </Field>
        <Field label="Your role">
          <select value={form.role} onChange={set("role")}>{(account.roles ?? []).map((r) => <option key={r}>{r}</option>)}</select>
        </Field>
        <Field label="City"><input value={form.city} onChange={set("city")} placeholder="Bekasi" /></Field>
        <Field label="Plant scale"><input value={form.plant_scale} onChange={set("plant_scale")} placeholder="4-10 production lines" /></Field>
      </div>
      <div className="row">
        <button className="primary" onClick={submit} disabled={busy || form.factory_name.trim().length < 2}>{busy ? "Sending…" : "Send registration"}</button>
      </div>
      {err && <p className="error small" role="alert">{err}</p>}
    </Card>
  );
}

function Onboarding({ account }: { account: Account }) {
  const { setAccount } = useAuth();
  const { data } = useData<{ templates: Template[] }>("/api/onboarding/templates");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const call = async (path: string, body?: unknown) => {
    setBusy(true);
    setErr(null);
    try {
      setAccount(await post<Account>(path, body));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const ob = account.onboarding;
  return (
    <>
      <Card title={`Onboarding: ${account.factory_name}`} actions={ob && <Badge tone={ob.done === ob.total ? "good" : "info"}>{ob.done} of {ob.total} done</Badge>}>
        <p className="muted">{account.role} · {account.industry}{account.city ? ` · ${account.city}` : ""} · approved by {account.decided_by}</p>
        {!account.template_id && (
          <>
            <h3>Choose a factory template</h3>
            {(data?.templates ?? []).map((t) => (
              <article key={t.template_id} className="action">
                <header><h3>{t.name}</h3><Badge tone="info">{t.industry}</Badge></header>
                <p>{t.summary}</p>
                <p className="muted small">Starter master data: {t.master_data.join(" · ")}</p>
                <div className="row"><button className="primary" disabled={busy} onClick={() => call("/api/onboarding/apply-template", { template_id: t.template_id })}>{busy ? "Applying…" : "Apply this template"}</button></div>
              </article>
            ))}
          </>
        )}
        <ol className="onboarding-steps">
          {(ob?.steps ?? []).map((s) => (
            <li key={s.step_id}>
              <Badge tone={s.done ? "good" : "neutral"}>{s.done ? "done" : "to do"}</Badge> <strong>{s.title}</strong>
              <span className="muted small"> · {s.detail}</span>
              {!s.done && s.link && (
                <span className="row">
                  <Link to={s.link}>Open</Link>
                  <button className="ghost small" disabled={busy} onClick={() => call(`/api/onboarding/steps/${s.step_id}`)}>Mark done</button>
                </span>
              )}
            </li>
          ))}
        </ol>
      </Card>
      {err && <p className="error" role="alert">{err}</p>}
    </>
  );
}

export default function AccountPage() {
  const { user, account, signOut, refresh } = useAuth();
  const [checking, setChecking] = useState(false);
  const recheck = async () => {
    setChecking(true);
    await get("/api/account/me").catch(() => null);
    await refresh();
    setChecking(false);
  };
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Account & onboarding</h1>
          <p className="muted">Sign up with Google → admin approval → onboarding on a factory template → first workflow.</p>
        </div>
        {user && <button className="ghost" onClick={signOut}>Sign out {user.email}</button>}
      </div>
      {!user && <SignIn />}
      {user && account?.status === "unregistered" && <Register account={account} />}
      {user && account?.status === "pending" && (
        <Card title="Waiting for approval">
          <p>Thank you, {account.name}. The registration for <strong>{account.factory_name}</strong> waits for an admin. You get an email when it is approved; meanwhile the demo stays open as a guest.</p>
          <div className="row"><button onClick={recheck} disabled={checking}>{checking ? "Checking…" : "Check again"}</button><Link to="/">Explore the demo</Link></div>
        </Card>
      )}
      {user && account?.status === "rejected" && (
        <Card title="Registration not approved"><p>{account.note ?? "The admin did not approve this registration."}</p></Card>
      )}
      {user && account?.status === "approved" && <Onboarding account={account} />}
      {user && account?.is_admin && <p><Link to="/admin/registrations">Open the registration approvals</Link></p>}
    </>
  );
}
