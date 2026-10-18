import { useEffect } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";
import { formatIdrCompact } from "../lib/format";
import { useData } from "../lib/store";

type Briefing = { as_of: string; oee: { molding: { oee_pct: number }; worst_machine: string; worst_machine_oee_pct: number }; top_losses: { loss_idr: number }[]; risks: { title: string }[] };

const MODULES: [string, string][] = [
  ["Demand Forecast", "BigQuery ML forecast; reads customer PO PDFs and explains the trend."],
  ["Production Planning", "OR-Tools plans and re-plans around the bottleneck machine."],
  ["MES / Shop Floor", "3D digital twin, OEE, downtime Pareto and breakdown prediction."],
  ["Material Supply", "Plan × BOM shortages; the purchasing agent drafts the POs."],
  ["Purchasing", "Vendor scorecard, vendor emails, POs emailed after approval."],
  ["Quality Control", "Gemini Vision checks bumper photos and holds the suspect resin lot."],
  ["Delivery", "Orders at risk, new ship dates and the customer notice."],
];

const JOURNEY: [string, string][] = [
  ["1", "Sign up with Google"],
  ["2", "Admin approves your factory"],
  ["3", "Apply a factory template"],
  ["4", "Agents propose, you approve, they act"],
];

export default function LandingPage() {
  const { enabled, user, account, signIn } = useAuth();
  const navigate = useNavigate();
  const { data: b } = useData<Briefing>("/api/briefing");
  // after the Google popup, continue to registration or onboarding
  useEffect(() => {
    if (user && account) navigate("/account");
  }, [user, account, navigate]);
  const start = () => (enabled ? signIn() : navigate("/account"));
  return (
    <div className="lp">
      <header className="lp-nav">
        <div className="lp-wrap lp-nav-row">
          <img src="/brand/factory-vision-logo.svg" alt="Factory Vision" className="lp-logo" />
          <nav className="lp-links">
            <a href="#modules">Modules</a>
            <a href="#journey">How it works</a>
            <Link to="/">Live demo</Link>
          </nav>
          <div className="lp-nav-cta">
            <button className="lp-btn-ghost" onClick={start}>Sign in</button>
            <button className="lp-btn-primary" onClick={start}>Sign up</button>
          </div>
        </div>
      </header>

      <section className="lp-hero lp-wrap">
        <span className="lp-eyebrow">AI copilot for manufacturing operations</span>
        <h1>
          See your factory.
          <br />
          <span>Let the agents act on it.</span>
        </h1>
        <p className="lp-desc">
          Eight Gemini agents connect demand, planning, the shop floor, material, purchasing, quality and delivery. They propose one plan with its impact in Rupiah,
          scrap and CO2e, and after you approve, they issue the POs and email the people who need to know.
        </p>
        <div className="lp-cta">
          <button className="lp-btn-primary lp-big" onClick={start}>Sign up with Google</button>
          <Link to="/" className="lp-btn-secondary lp-big">Try the live demo</Link>
        </div>
        {!enabled && <p className="lp-note">Sign-in is not configured on this server; the guest demo is open.</p>}

        <div className="lp-frame" aria-label="Plant Briefing preview">
          <div className="lp-frame-bar">
            <span className="lp-dots"><i /><i /><i /></span>
            <span className="lp-url">factory-vision-ai / plant-briefing</span>
          </div>
          <div className="lp-frame-body">
            <div className="lp-kpi"><small>OEE molding, yesterday</small><strong>{b ? `${b.oee.molding.oee_pct.toFixed(1)}%` : "…"}</strong></div>
            <div className="lp-kpi bad"><small>Worst machine</small><strong>{b ? `${b.oee.worst_machine} · OEE ${b.oee.worst_machine_oee_pct.toFixed(1)}%` : "…"}</strong></div>
            <div className="lp-kpi"><small>Top-3 losses</small><strong>{b ? formatIdrCompact(b.top_losses.slice(0, 3).reduce((a, l) => a + l.loss_idr, 0)) : "…"}</strong></div>
            <ul className="lp-risks">{(b?.risks ?? []).slice(0, 3).map((r) => <li key={r.title}>{r.title}</li>)}</ul>
          </div>
        </div>
      </section>

      <section id="modules" className="lp-section lp-wrap">
        <h2>Seven modules, one Order-to-Delivery chain</h2>
        <div className="lp-grid">
          {MODULES.map(([t, d]) => (
            <article key={t} className="lp-card">
              <h3>{t}</h3>
              <p>{d}</p>
            </article>
          ))}
        </div>
      </section>

      <section id="journey" className="lp-section lp-wrap">
        <h2>From sign-up to your first decision</h2>
        <ol className="lp-journey">
          {JOURNEY.map(([n, t]) => (
            <li key={n}><span>{n}</span>{t}</li>
          ))}
        </ol>
      </section>

      <section className="lp-band">
        <div className="lp-wrap">
          <h2>Ready to see your plant?</h2>
          <div className="lp-cta">
            <button className="lp-btn-light lp-big" onClick={start}>Sign up with Google</button>
            <Link to="/" className="lp-band-link">Try the live demo →</Link>
          </div>
        </div>
      </section>

      <footer className="lp-foot lp-wrap">
        Built on Gemini (Vertex AI), BigQuery ML, Cloud Run, Firestore, Pub/Sub, Cloud Storage and Firebase Auth. All company, customer and vendor names are
        fictional.
      </footer>
    </div>
  );
}
