import { useState } from "react";
import { AskAgent } from "../components/agents";
import { Bars } from "../components/charts";
import { Badge, Card, Kpi, Source, Status, Table, num, pct } from "../components/ui";
import { errorText, postForm } from "../lib/api";
import { useApp, useData } from "../lib/store";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;
type Photo = { photo_id: string; file: string; sku: string; model: string; position: string; color: string };

export const VERDICT_TONE: Record<string, "good" | "bad" | "warn"> = { OK: "good", WRONG_COLOR: "bad", WRONG_PART: "bad", SCRAP: "bad" };

/** QC-1: verify a Vidad bumper photo against an order line (model, colour, defects). */
export function PhotoInspect() {
  const { live, mode } = useApp();
  const photos = useData<{ photos: Photo[] }>("/api/photos");
  const [photoId, setPhotoId] = useState("");
  const [expected, setExpected] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [res, setRes] = useState<Any>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const list = photos.data?.photos ?? [];
  const sel = list.find((p) => p.photo_id === photoId) ?? list[0];
  const disabled = !live || !mode?.ai_enabled;
  const inspect = async () => {
    if (!sel) return;
    setBusy(true);
    setErr(null);
    const f = new FormData();
    if (file) f.append("photo", file);
    else f.append("photo_id", sel.photo_id);
    f.append("expected_sku", sel.sku);
    f.append("expected_color", expected || sel.color);
    try {
      setRes(await postForm("/api/qc/inspect", f));
    } catch (e) {
      setErr(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Photo inspection (Gemini Vision)">
      {!list.length && <p className="muted">The Vidad photo pack is not on this server.</p>}
      {sel && (
        <div className="inspect">
          <img src={file ? URL.createObjectURL(file) : `/api/photos/${sel.file}`} alt={`${sel.sku} ${sel.color}`} />
          <div>
            <label>
              Photo{" "}
              <select value={sel.photo_id} onChange={(e) => (setPhotoId(e.target.value), setFile(null), setRes(null))}>
                {list.map((p) => <option key={p.photo_id} value={p.photo_id}>{p.photo_id} · {p.sku} {p.color}</option>)}
              </select>
            </label>
            <label>
              Order line expects colour{" "}
              <input value={expected} placeholder={sel.color} onChange={(e) => setExpected(e.target.value.toUpperCase())} size={5} />
            </label>
            <label>
              Or upload <input type="file" accept="image/jpeg,image/png" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
            <button className="primary" onClick={inspect} disabled={disabled || busy}>{busy ? "Inspecting…" : `Inspect as ${sel.sku} ${expected || sel.color}`}</button>
            {disabled && <p className="muted small">Live mode only (one Gemini call per photo).</p>}
            {err && <p className="error">{err}</p>}
            {res && (
              <div className="verdict">
                <Badge tone={VERDICT_TONE[res.verdict] ?? "warn"}>{res.verdict}</Badge> model {res.model} {res.position}, colour: {res.color_description} (confidence {pct(100 * res.confidence, 0)})
                {res.defects.length > 0 && <ul>{res.defects.map((d: Any, i: number) => <li key={i}>{d.defect_name} ({d.ng_group}, {d.category}) → {d.disposition}, {pct(100 * d.confidence, 0)}</li>)}</ul>}
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

export default function QualityPage() {
  const { data, error, loading } = useData<Any>("/api/modules/quality/summary");
  const c = data?.correlation;
  return (
    <>
      <h1>Quality Control</h1>
      <Status loading={loading} error={error} />
      {data && (
        <>
          <div className="kpis">
            <Kpi label="Defect rate (45 days)" value={pct(c.defect_rate_pct, 2)} sub={`${num(c.inspected_qty)} inspected`} />
            <Kpi label="Suspect resin lot" value={c.suspect_lot ?? "none"} tone={c.suspect_lot ? "bad" : "good"} />
            <Kpi label="Units from that lot in stock" value={num(c.hold_candidates?.units_in_stock)} />
            <Kpi label="Quality holds" value={data.holds.length} sub={data.holds.map((h: Any) => `${h.resin_lot_id} ${h.status}`).join(", ") || "none"} tone={data.holds.length ? "warn" : undefined} />
          </div>
          <div className="grid2">
            <Card title="Defects by resin lot">
              <Table
                cols={[{ key: "lot", label: "Lot" }, { key: "d", label: "Defect" }, { key: "r", label: "Rate vs plant", align: "right" }, { key: "l", label: "Lift", align: "right" }]}
                rows={c.lot_findings.map((f: Any) => ({ lot: f.resin_lot_id, d: `${f.defect_name} (${f.ng_group})`, r: `${pct(f.rate_pct, 2)} vs ${pct(f.overall_rate_pct, 2)}`, l: <Badge tone="bad">{f.lift}×</Badge> }))}
              />
            </Card>
            <Card title="Defects vs humidity (> 80% RH)">
              <Table
                cols={[{ key: "d", label: "Defect" }, { key: "r", label: "Humid vs normal", align: "right" }, { key: "l", label: "Lift", align: "right" }]}
                rows={c.humidity_findings.map((f: Any) => ({ d: f.defect_name, r: `${pct(f.rate_high_humidity_pct, 2)} vs ${pct(f.rate_normal_pct, 2)}`, l: <Badge tone="warn">{f.lift}×</Badge> }))}
              />
            </Card>
          </div>
          <Card title="Defect Pareto">
            <Bars items={c.defect_pareto.map((d: Any) => ({ label: d.defect_name, value: d.qty }))} unit=" pcs" />
            <Source>{c.source}</Source>
          </Card>
          <PhotoInspect />
          <AskAgent module="quality" placeholder="Which resin lot explains the gas mark spike, and what should we hold?" />
        </>
      )}
    </>
  );
}
