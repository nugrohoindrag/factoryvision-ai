/** Line icons for KPI cards and hero actions, drawn on a 24-unit grid and coloured by currentColor. */

const PATHS: Record<string, string> = {
  gauge: "M4 16a8 8 0 1 1 16 0M12 16l4-5M12 16h.01",
  clock: "M12 4a8 8 0 1 0 0 16a8 8 0 1 0 0-16M12 8v4l3 2",
  bolt: "M13 3L5 14h6l-1 7l8-11h-6z",
  check: "M12 3l7 3v5c0 4.5-3 8-7 10c-4-2-7-5.5-7-10V6zM9 12l2 2l4-4",
  box: "M4 8l8-4l8 4v8l-8 4l-8-4zM4 8l8 4l8-4M12 12v8",
  timer: "M12 7a7 7 0 1 0 0 14a7 7 0 1 0 0-14M12 11v3M10 3h4M18.5 6.5l-1.4 1.4",
  alert: "M12 4l9 16H3zM12 10v4M12 17h.01",
  truck: "M3 7h11v9H3zM14 10h4l3 3v3h-7M7 19a1.5 1.5 0 1 0 0-3a1.5 1.5 0 1 0 0 3M17 19a1.5 1.5 0 1 0 0-3a1.5 1.5 0 1 0 0 3",
  factory: "M3 20V10l5 3V10l5 3V6h3l1 6h4v8zM7 17h2M12 17h2M17 17h2",
  inspect: "M10 4a6 6 0 1 0 0 12a6 6 0 1 0 0-12M14.5 14.5L20 20",
  inbox: "M4 13l2.5-8h11L20 13v6H4zM4 13h5l1 2h4l1-2h5",
};

export function Icon({ name, size = 16 }: { name: keyof typeof PATHS | string; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.9} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name] ?? PATHS.gauge} />
    </svg>
  );
}
