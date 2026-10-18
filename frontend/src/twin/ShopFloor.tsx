/** MES page face: the 3D twin, live camera feeds, AI observations, event feed, conveyor inspection. */

import { OrbitControls } from "@react-three/drei";
import { Canvas } from "@react-three/fiber";
import { useCallback, useEffect, useRef, useState } from "react";
import { Badge, Card } from "../components/ui";
import { VERDICT_TONE } from "../pages/Quality";
import { ApiError, errorText, get, postForm } from "../lib/api";
import { useApp } from "../lib/store";
import type { Observation } from "../lib/types";
import { CAMERAS, CameraRig, frameDiff, signature, toJpeg, type CameraId, type Capture } from "./cameras";
import PlantScene, { ANDON_HEX } from "./Scene";
import { useTwin } from "./useTwin";

const MES_CAMS: CameraId[] = ["CAM-Molding", "CAM-WIP", "CAM-Warehouse"];
const DIFF_THRESHOLD = 0.004;
const DEBOUNCE_MS = 30_000;
const LIVE_INSPECTION_S = 60;

type CamResult = { observations: Observation[]; cached: boolean; calls_left?: number; score?: { precision: number | null; missed: string[] } };

export default function ShopFloor() {
  const { snap, events, error, paused, setPaused } = useTwin();
  const { live, mode } = useApp();
  const ai = live && !!mode?.ai_enabled;
  const capture = useRef<Capture | null>(null);
  const feeds = useRef<Record<string, HTMLCanvasElement | null>>({});
  const lastSig = useRef<Record<string, Float32Array | null>>({});
  const lastHash = useRef<Record<string, string>>({});
  const lastCall = useRef<Record<string, number>>({});
  const [looking, setLooking] = useState<Record<string, boolean>>({});
  const [results, setResults] = useState<Record<string, CamResult>>({});
  const [visionErr, setVisionErr] = useState<string | null>(null);
  const [photos, setPhotos] = useState<Record<string, string>>({});
  const snapRef = useRef(snap);
  snapRef.current = snap;

  useEffect(() => {
    get<{ photos: { photo_id: string; file: string }[] }>("/api/photos")
      .then((r) => setPhotos(Object.fromEntries(r.photos.map((p) => [p.photo_id, p.file]))))
      .catch(() => setPhotos({}));
  }, []);

  const onReady = useCallback((c: Capture) => {
    capture.current = c;
  }, []);

  const analyze = useCallback(async (cam: CameraId, force: boolean) => {
    const s = snapRef.current;
    if (!capture.current || !s) return;
    if (!force && Date.now() - (lastCall.current[cam] ?? 0) < DEBOUNCE_MS) return;
    lastCall.current[cam] = Date.now();
    setLooking((l) => ({ ...l, [cam]: true }));
    try {
      const full = capture.current(cam, 1280, 720);
      lastSig.current[cam] = signature(full);
      const f = new FormData();
      f.append("camera_id", cam);
      f.append("state_hash", s.camera_hashes?.[cam] ?? s.state_hash);
      f.append("frame", await toJpeg(full), `${cam}.jpg`);
      const r = await postForm<CamResult>("/api/vision/frame", f);
      setResults((x) => ({ ...x, [cam]: r }));
      setVisionErr(null);
    } catch (e) {
      if (!(e instanceof ApiError && e.body.code === "FV-VIS-002")) setVisionErr(errorText(e));
    } finally {
      window.setTimeout(() => setLooking((l) => ({ ...l, [cam]: false })), 1200);
    }
  }, []);

  // ~1 fps feeds and local frame diff; Gemini only on a visible change of the camera's own state (pseudo-streaming).
  useEffect(() => {
    const id = window.setInterval(() => {
      const s = snapRef.current;
      if (!capture.current || !s || document.visibilityState !== "visible") return;
      for (const cam of [...MES_CAMS, "CAM-Inspection" as CameraId]) {
        const thumb = capture.current(cam, 320, 180);
        const el = feeds.current[cam];
        el?.getContext("2d")?.drawImage(thumb, 0, 0, el.width, el.height);
        if (!ai || cam === "CAM-Inspection") continue;
        const hash = s.camera_hashes?.[cam] ?? "";
        const changed = frameDiff(lastSig.current[cam] ?? null, signature(thumb)) > DIFF_THRESHOLD;
        if (hash !== lastHash.current[cam] && changed) {
          lastHash.current[cam] = hash;
          analyze(cam, false);
        }
      }
    }, 1000);
    return () => window.clearInterval(id);
  }, [ai, analyze]);

  const imm04 = snap?.machines.find((m) => m.machine_id === "IMM-04");
  return (
    <>
      <Card
        className="twin-card"
        title={<>Digital twin · {snap ? `${snap.sim_ts.replace("T", " ").slice(0, 16)} WIB` : "starting…"} {snap && snap.scenario !== "baseline" && <Badge tone="bad">{snap.scenario.replaceAll("_", " ")}</Badge>}</>}
        actions={
          <div className="row">
            <button onClick={() => setPaused(!paused)}>{paused ? "Resume" : "Pause"}</button>
            <span className="muted small">1 s = 1 sim minute · advances only while this page is open</span>
          </div>
        }
      >
        {error && <p className="error">{error}</p>}
        <div className="twin">
          <div className="twin-canvas">
            {snap && (
              <Canvas camera={{ position: [0, 20, 24], fov: 50 }} dpr={[1, 1.5]} gl={{ preserveDrawingBuffer: true, antialias: true }}>
                <PlantScene snap={snap} photos={photos} />
                <OrbitControls target={[0, 0, -2]} maxPolarAngle={Math.PI / 2.1} />
                <CameraRig onReady={onReady} />
              </Canvas>
            )}
          </div>
          <div className="feeds">
            {MES_CAMS.map((cam) => (
              <figure key={cam} className={`feed ${looking[cam] ? "looking" : ""}`}>
                <canvas ref={(el) => void (feeds.current[cam] = el)} width={320} height={180} aria-label={`${cam} live feed`} />
                <figcaption>
                  <span>{cam}</span>
                  <button className="ghost small" disabled={!ai || looking[cam]} onClick={() => analyze(cam, true)} title={ai ? "Send this frame to the Twin Vision Analyzer" : "Live mode only"}>
                    Ask AI to look
                  </button>
                </figcaption>
              </figure>
            ))}
          </div>
        </div>
        <div className="legend small">
          {Object.entries({ green: "Running", yellow: "Slow (<85%) or Setup", red: "Downtime", blue: "Maintenance", grey: "Idle/Offline" }).map(([c, l]) => (
            <span key={c}><i style={{ background: ANDON_HEX[c] }} /> {l}</span>
          ))}
          {imm04 && <span className="muted">IMM-04: {imm04.status}{imm04.reason_code ? ` (${imm04.reason_code})` : ""}, cycle {imm04.cycle_s} s, performance {imm04.performance_pct}%</span>}
        </div>
      </Card>
      <div className="grid2">
        <Card title="AI observations (Twin Vision Analyzer)">
          {!ai && <p className="muted small">Live mode: the analyzer ({mode?.models.vision ?? "gemini-3.5-flash-lite"}) is called only when a camera's view changes, or when you press "Ask AI to look". Replay never calls Gemini.</p>}
          {visionErr && <p className="error">{visionErr}</p>}
          {MES_CAMS.filter((c) => results[c]).map((cam) => (
            <div key={cam} className="obs">
              <strong>{cam}</strong> {results[cam].cached && <Badge>cached</Badge>}{" "}
              {results[cam].score?.precision != null && <Badge tone={(results[cam].score!.precision ?? 0) >= 0.8 ? "good" : "warn"}>{Math.round(100 * results[cam].score!.precision!)}% match with simulator</Badge>}
              {results[cam].observations.length ? (
                <ul>{results[cam].observations.map((o, i) => <li key={i}><Badge tone={o.severity === "high" ? "bad" : o.severity === "medium" ? "warn" : "neutral"}>{o.type}</Badge> {o.subject}{o.value != null ? ` = ${o.value}` : ""} <span className="muted">({Math.round(o.confidence * 100)}%)</span></li>)}</ul>
              ) : <p className="muted small">Nothing abnormal.</p>}
            </div>
          ))}
        </Card>
        <Card title="Plant events">
          <ul className="events small">
            {events.length === 0 && <li className="muted">No events yet.</li>}
            {events.map((e, i) => (
              <li key={i}><span className="muted">{e.sim_ts.slice(11, 16)}</span> <code>{e.event_type}</code> {e.subject} {e.event_type === "machine.status" ? `→ ${String(e.payload.status)}${e.payload.reason_code ? ` (${String(e.payload.reason_code)})` : ""}` : ""}{e.event_type === "wip.low" ? `: ${String(e.payload.units)} units` : ""}</li>
            ))}
          </ul>
        </Card>
      </div>
      {snap && <InspectionStation snap={snap} ai={ai} feedRef={(el) => void (feeds.current["CAM-Inspection"] = el)} />}
    </>
  );
}

/** CAM-Inspection (QC-5): real Vidad photos on the INS-01 conveyor, verified against the order line.
 * Uses 1 fps-class REST sampling: each new bumper at the inspection point is sent once, for at most 60 s per start. */
function InspectionStation({ snap, ai, feedRef }: { snap: NonNullable<ReturnType<typeof useTwin>["snap"]>; ai: boolean; feedRef: (el: HTMLCanvasElement | null) => void }) {
  const [until, setUntil] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [log, setLog] = useState<{ seq: number; sku: string; color: string; expected: string; verdict: string; detail: string }[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const seen = useRef(new Set<number>());
  const busy = useRef(false);
  const running = until > now;
  const head = snap.conveyor[0];

  useEffect(() => {
    if (!running) return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [running]);

  useEffect(() => {
    if (!running || !head || !head.photo_id || seen.current.has(head.seq) || busy.current) return;
    seen.current.add(head.seq);
    busy.current = true;
    const f = new FormData();
    f.append("photo_id", head.photo_id);
    f.append("expected_sku", head.sku);
    f.append("expected_color", head.expected_color);
    postForm<{ verdict: string; color_description: string; defects: { defect_name: string }[] }>("/api/qc/inspect", f)
      .then((r) => setLog((l) => [{ seq: head.seq, sku: head.sku, color: head.color, expected: head.expected_color, verdict: r.verdict, detail: r.defects.map((d) => d.defect_name).join(", ") || r.color_description }, ...l].slice(0, 12)))
      .catch((e) => (setErr(errorText(e)), setUntil(0)))
      .finally(() => (busy.current = false));
  }, [running, head]);

  return (
    <Card
      title="Final inspection INS-01 (CAM-Inspection)"
      actions={
        <button className="primary" disabled={!ai || running} onClick={() => (setErr(null), setUntil(Date.now() + LIVE_INSPECTION_S * 1000), setNow(Date.now()))}>
          {running ? `Inspecting… ${Math.ceil((until - now) / 1000)} s` : `Start live inspection (${LIVE_INSPECTION_S} s)`}
        </button>
      }
    >
      <div className="inspection">
        <canvas ref={feedRef} width={320} height={180} aria-label="CAM-Inspection live feed" className="feed-big" />
        <div>
          <p className="small muted">
            Real Vidad bumper photos on the conveyor. Each bumper is checked for model and colour against its order line before it may enter SGL (finished stock).
            {!ai && " Live mode only."}
          </p>
          {err && <p className="error">{err}</p>}
          <table>
            <thead><tr><th>#</th><th>Bumper</th><th>Order expects</th><th>Verdict</th></tr></thead>
            <tbody>
              {snap.conveyor.slice(0, 1).map((c) => (
                <tr key={`now-${c.seq}`} className="muted"><td>{c.seq}</td><td>{c.sku} (at INS-01 now)</td><td>{c.expected_color}</td><td>…</td></tr>
              ))}
              {log.map((l) => (
                <tr key={l.seq}><td>{l.seq}</td><td>{l.sku}</td><td>{l.expected}</td><td><Badge tone={VERDICT_TONE[l.verdict] ?? "warn"}>{l.verdict}</Badge> <span className="small muted">{l.detail}</span></td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </Card>
  );
}
