import type { ReactNode } from "react";
import { Sparkline } from "./charts";
import { Icon } from "./icons";
import { formatIdr, formatIdrCompact } from "../lib/format";
import type { Impact } from "../lib/types";

export function Card({ title, children, actions, className = "" }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "warn" | "bad" }) {
  return (
    <div className={`kpi ${tone ?? ""}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export type Tone = "good" | "warn" | "bad" | "info" | "accent";

/** Executive KPI card: value, change against the day before, status and a sparkline of the real daily series. */
export function MetricCard({ label, icon, value, tone, delta, sub, spark }: { label: string; icon: string; value: ReactNode; tone: Tone; delta?: { text: string; good: boolean | null }; sub?: ReactNode; spark?: number[] }) {
  return (
    <div className={`metric ${tone}`}>
      <div className="metric-head">
        <span className="metric-label">{label}</span>
        <span className="metric-icon"><Icon name={icon} size={14} /></span>
      </div>
      <div className="metric-body">
        <div>
          <div className="metric-value">{value}</div>
          <div className="metric-meta">
            {delta && <span className={delta.good == null ? "flat" : delta.good ? "up" : "down"}>{delta.text}</span>}
            {sub && <span>{sub}</span>}
          </div>
        </div>
        {spark && <Sparkline values={spark} label={`${label}, last ${spark.length} production days`} />}
      </div>
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "info" }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Status({ loading, error }: { loading: boolean; error: string | null }) {
  if (error) return <p className="error" role="alert">{error}</p>;
  if (loading) return <p className="muted">Loading…</p>;
  return null;
}

export function Table({ cols, rows, empty = "Nothing to show." }: { cols: { key: string; label: string; align?: "right" }[]; rows: Record<string, ReactNode>[]; empty?: string }) {
  if (!rows.length) return <p className="muted">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>{cols.map((c) => <th key={c.key} className={c.align}>{c.label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c.key} className={c.align}>{r[c.key]}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Modal dialog: Escape and the backdrop close it; the title names it for screen readers. */
export function Dialog({ open, title, onClose, children, wide = false }: { open: boolean; title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  if (!open) return null;
  return (
    <div className="dialog-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()} onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        e.stopPropagation(); // a dialog opened from a dialog closes alone
        onClose();
      }}>
      <div className={`dialog ${wide ? "wide" : ""}`} role="dialog" aria-modal="true" aria-label={title}>
        <header className="dialog-head">
          <h2>{title}</h2>
          <button className="ghost" aria-label="Close" onClick={onClose}>✕</button>
        </header>
        <div className="dialog-body">{children}</div>
      </div>
    </div>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="field">
      <span className="muted small">{label}</span>
      <strong>{children}</strong>
    </div>
  );
}

export function Source({ children }: { children: ReactNode }) {
  return <p className="source">Source: {children}</p>;
}

export function ImpactRow({ impact, compact = false }: { impact: Impact; compact?: boolean }) {
  const r = impact.rupiah_idr ?? 0;
  return (
    <div className="impact">
      <span className={r >= 0 ? "pos" : "neg"} title={formatIdr(r)}>{compact ? formatIdrCompact(r) : formatIdr(r)}</span>
      <span title="Scrap avoided">{(impact.scrap_kg ?? 0).toLocaleString("en-US")} kg scrap</span>
      <span title="Energy saved">{(impact.kwh ?? 0).toLocaleString("en-US")} kWh</span>
      <span title="CO2e avoided">{(impact.co2e_kg ?? 0).toLocaleString("en-US")} kg CO₂e</span>
    </div>
  );
}

/** Tiny Markdown subset for agent text: **bold**, bullets, numbered lists, paragraphs. */
export function Markdown({ text }: { text: string }) {
  const blocks = text.split(/\n{2,}/);
  const inline = (s: string) => s.split(/(\*\*[^*]+\*\*)/g).map((p, i) => (p.startsWith("**") ? <strong key={i}>{p.slice(2, -2)}</strong> : p));
  return (
    <div className="md">
      {blocks.map((b, i) => {
        const lines = b.split("\n").filter(Boolean);
        if (lines.every((l) => /^\s*[-*] /.test(l))) return <ul key={i}>{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*] /, ""))}</li>)}</ul>;
        if (lines.length > 1 && lines.slice(1).every((l) => /^\s*\d+\. /.test(l)) && !/^\s*\d+\. /.test(lines[0]))
          return (
            <div key={i}>
              <p>{inline(lines[0])}</p>
              <ol>{lines.slice(1).map((l, j) => <li key={j}>{inline(l.replace(/^\s*\d+\. /, ""))}</li>)}</ol>
            </div>
          );
        if (lines.every((l) => /^\s*\d+\. /.test(l))) return <ol key={i}>{lines.map((l, j) => <li key={j}>{inline(l.replace(/^\s*\d+\. /, ""))}</li>)}</ol>;
        if (lines.length > 1 && lines.slice(1).every((l) => /^\s*[-*] /.test(l)))
          return (
            <div key={i}>
              <p>{inline(lines[0])}</p>
              <ul>{lines.slice(1).map((l, j) => <li key={j}>{inline(l.replace(/^\s*[-*] /, ""))}</li>)}</ul>
            </div>
          );
        return <p key={i}>{lines.map((l, j) => <span key={j}>{inline(l)}{j < lines.length - 1 && <br />}</span>)}</p>;
      })}
    </div>
  );
}

export const pct = (v: number | null | undefined, d = 1) => (v == null ? "–" : `${v.toFixed(d)}%`);
export const num = (v: number | null | undefined) => (v == null ? "–" : Math.round(v).toLocaleString("en-US"));
