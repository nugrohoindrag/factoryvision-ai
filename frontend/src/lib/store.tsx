/** App-wide state: Replay/Live mode, the running disruption, and a version that bumps when actions change. */

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { errorText, get, post, postStream } from "./api";
import type { Mode, Recording, TimelineItem } from "./types";

export type RunState = {
  status: "idle" | "running" | "done" | "error";
  source: "replay" | "live";
  timeline: TimelineItem[];
  text: string;
  recordedWith?: string;
  planId?: string;
  error?: string;
};

type Ctx = {
  mode: Mode | null;
  live: boolean;
  setLive: (v: boolean) => void;
  version: number;
  bump: () => void;
  pending: number;
  run: RunState;
  startDisruption: () => Promise<void>;
  reset: () => Promise<void>;
  panelOpen: boolean;
  setPanelOpen: (v: boolean) => void;
};

const AppCtx = createContext<Ctx | null>(null);
const IDLE: RunState = { status: "idle", source: "replay", timeline: [], text: "" };
const REPLAY_SPEED = 1.5; // the recording is paced like a live run; play it a bit faster

export function AppProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [live, setLive] = useState(false);
  const [version, setVersion] = useState(0);
  const [pending, setPending] = useState(0);
  const [run, setRun] = useState<RunState>(IDLE);
  const [panelOpen, setPanelOpen] = useState(false);
  const timers = useRef<number[]>([]);

  const bump = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    get<Mode>("/api/mode").then(setMode).catch(() => setMode(null));
  }, []);

  useEffect(() => {
    get<{ pending: number }>("/api/actions")
      .then((r) => setPending(r.pending))
      .catch(() => setPending(0));
  }, [version]);

  const clearTimers = () => {
    timers.current.forEach((t) => window.clearTimeout(t));
    timers.current = [];
  };

  const startDisruption = useCallback(async () => {
    clearTimers();
    setPanelOpen(true);
    if (!live) {
      setRun({ ...IDLE, status: "running", source: "replay" });
      try {
        const rec = await post<Recording>("/api/replay/disruption_response");
        bump();
        rec.timeline.forEach((item) => {
          timers.current.push(window.setTimeout(() => setRun((r) => ({ ...r, timeline: [...r.timeline, item] })), item.t_ms / REPLAY_SPEED));
        });
        const end = (rec.timeline.at(-1)?.t_ms ?? 0) / REPLAY_SPEED + 600;
        timers.current.push(window.setTimeout(() => setRun((r) => ({ ...r, status: "done", text: rec.text, recordedWith: rec.recorded_with, planId: rec.plan_id })), end));
      } catch (e) {
        setRun({ ...IDLE, status: "error", error: errorText(e) });
      }
      return;
    }
    setRun({ ...IDLE, status: "running", source: "live" });
    try {
      await postStream("/api/disruptions/disruption_response", (item) => {
        const kind = item.kind as string;
        if (kind === "run_start") setRun((r) => ({ ...r, planId: item.plan_id as string }));
        else if (kind === "done") setRun((r) => ({ ...r, status: "done", text: item.text as string, recordedWith: "live run on Vertex AI" }));
        else if (kind === "error") {
          const er = item.error as { code: string; title: string; message: string };
          setRun((r) => ({ ...r, status: "error", error: `${er.title} (${er.code}): ${er.message}` }));
        }
        else setRun((r) => ({ ...r, timeline: [...r.timeline, item as unknown as TimelineItem] }));
      });
    } catch (e) {
      setRun((r) => ({ ...r, status: "error", error: errorText(e) }));
    }
    bump();
  }, [live, bump]);

  const reset = useCallback(async () => {
    clearTimers();
    await post("/api/demo/reset", { scenario: "baseline" });
    setRun(IDLE);
    bump();
  }, [bump]);

  const value = useMemo(
    () => ({ mode, live, setLive, version, bump, pending, run, startDisruption, reset, panelOpen, setPanelOpen }),
    [mode, live, version, bump, pending, run, startDisruption, reset, panelOpen],
  );
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
}

/** Fetch JSON and refetch whenever the app version changes (after approve, reset, disruption). */
export function useData<T>(path: string | null): { data: T | null; error: string | null; loading: boolean } {
  const { version } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!path) return;
    let alive = true;
    setLoading(true);
    get<T>(path)
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(errorText(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [path, version]);
  return { data, error, loading };
}
