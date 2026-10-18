/** Lazy twin: the simulator only advances while this page is open and visible (TW-4). */

import { useCallback, useEffect, useRef, useState } from "react";
import { errorText, post } from "../lib/api";
import { useApp } from "../lib/store";
import type { Snapshot } from "../lib/types";

const TICK_MS = 3000;

export function useTwin() {
  const { version, bump } = useApp();
  const [snap, setSnap] = useState<Snapshot | null>(null);
  const [events, setEvents] = useState<Snapshot["events"]>([]);
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const busy = useRef(false);
  const lastCommands = useRef(0);

  const tick = useCallback(async (minutes?: number) => {
    if (busy.current) return;
    busy.current = true;
    try {
      const s = await post<Snapshot>("/api/twin/tick", minutes === undefined ? {} : { minutes });
      setSnap(s);
      setError(null);
      if (s.events.length) {
        setEvents((e) => [...s.events.slice().reverse(), ...e].slice(0, 40));
        const cmds = s.events.filter((e) => e.event_type === "command.applied").length;
        if (cmds) lastCommands.current += cmds;
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      busy.current = false;
    }
  }, []);

  // Approve, reset and disruption bump the version: fetch the new state at once.
  useEffect(() => {
    tick(0);
    setEvents([]);
  }, [version, tick]);

  useEffect(() => {
    if (paused) return;
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") tick();
    }, TICK_MS);
    return () => window.clearInterval(id);
  }, [paused, tick]);

  return { snap, events, error, paused, setPaused, tick, bump };
}
