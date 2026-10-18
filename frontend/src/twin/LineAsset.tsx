/**
 * Primitive 3D model of the molding line: an injection molding machine (IMM) and the resin dryer.
 *
 * Built from boxes and cylinders, like the rest of the plant scene. Parts are defined as plain data so the layout can
 * be tested without WebGL. Coordinates are local to the machine: origin at the centre of its base, y up, machine
 * width along x, depth along z. Sizes scale with the machine footprint (w, h, d) from LAYOUT.
 */

export type PartDef = {
  key: string;
  kind: "box" | "cylinder";
  args: [number, number, number];
  pos: [number, number, number];
  rot?: [number, number, number];
  color: string;
  metal?: number;
};

/** Top of the IMM assembly, as a multiple of the footprint height (the robot arm sits above the clamp). */
export const LINE_TOP = 1.1;

const HALF_PI = Math.PI / 2;
const STEEL = "#cbd5e1";
const BLUE = "#2563eb";
const WHITE = "#e5e7eb";
const BED = "#374151";

export function immParts(w: number, h: number, d: number): PartDef[] {
  const r = 0.07 * h;
  const barrelY = 0.6 * h;
  return [
    { key: "bed", kind: "box", args: [w, 0.25 * h, d], pos: [0, 0.125 * h, 0], color: BED },
    { key: "barrel", kind: "cylinder", args: [r, r, 0.36 * w], rot: [0, 0, HALF_PI], pos: [-0.3 * w, barrelY, 0], color: STEEL, metal: 0.6 },
    { key: "hopper", kind: "cylinder", args: [0.14 * h, 0.06 * h, 0.35 * h], pos: [-0.3 * w, barrelY + r + 0.175 * h, 0], color: WHITE },
    { key: "motor", kind: "box", args: [0.22 * w, 0.3 * h, 0.5 * d], pos: [-0.6 * w, 0.4 * h, 0], color: BLUE },
    { key: "clamp", kind: "box", args: [0.4 * w, 0.6 * h, 0.9 * d], pos: [0.15 * w, 0.55 * h, 0], color: BLUE },
    { key: "housing", kind: "box", args: [0.25 * w, 0.5 * h, 0.9 * d], pos: [0.42 * w, 0.5 * h, 0], color: WHITE },
    { key: "tie-bar-top-front", kind: "cylinder", args: [0.03 * h, 0.03 * h, 0.7 * w], rot: [0, 0, HALF_PI], pos: [0.25 * w, 0.8 * h, 0.35 * d], color: STEEL, metal: 0.6 },
    { key: "tie-bar-top-back", kind: "cylinder", args: [0.03 * h, 0.03 * h, 0.7 * w], rot: [0, 0, HALF_PI], pos: [0.25 * w, 0.8 * h, -0.35 * d], color: STEEL, metal: 0.6 },
    { key: "tie-bar-bottom-front", kind: "cylinder", args: [0.03 * h, 0.03 * h, 0.7 * w], rot: [0, 0, HALF_PI], pos: [0.25 * w, 0.3 * h, 0.35 * d], color: STEEL, metal: 0.6 },
    { key: "tie-bar-bottom-back", kind: "cylinder", args: [0.03 * h, 0.03 * h, 0.7 * w], rot: [0, 0, HALF_PI], pos: [0.25 * w, 0.3 * h, -0.35 * d], color: STEEL, metal: 0.6 },
    { key: "control-cabinet", kind: "box", args: [0.22 * w, 0.35 * h, 0.2 * d], pos: [-0.1 * w, 0.8 * h, 0.4 * d], color: WHITE },
    { key: "robot-column", kind: "box", args: [0.05 * w, 0.25 * h, 0.05 * d], pos: [0.35 * w, 0.975 * h, 0], color: STEEL, metal: 0.6 },
    { key: "robot-arm", kind: "box", args: [0.7 * w, 0.05 * h, 0.05 * d], pos: [0.1 * w, 1.1 * h - 0.025 * h, 0], color: STEEL, metal: 0.6 },
  ];
}

export function dryerParts(w: number, h: number, d: number): PartDef[] {
  return [
    { key: "frame", kind: "box", args: [0.9 * w, 0.08 * h, 0.9 * d], pos: [0, 0.04 * h, 0], color: "#64748b" },
    { key: "body", kind: "cylinder", args: [0.42 * w, 0.42 * w, 0.55 * h], pos: [0, 0.08 * h + 0.275 * h, 0], color: "#f8fafc", metal: 0.1 },
    { key: "feeder", kind: "cylinder", args: [0.25 * w, 0.2 * w, 0.25 * h], pos: [0, 0.08 * h + 0.55 * h + 0.125 * h, 0], color: WHITE },
    { key: "control-panel", kind: "box", args: [0.25 * w, 0.2 * h, 0.05 * d], pos: [0, 0.5 * h, 0.44 * d], color: "#111827" },
  ];
}

export function LineAsset({ kind, w, h, d }: { kind: "imm" | "dryer"; w: number; h: number; d: number }) {
  const parts = kind === "imm" ? immParts(w, h, d) : dryerParts(w, h, d);
  return (
    <group>
      {parts.map((p) => (
        <mesh key={p.key} position={p.pos} rotation={p.rot}>
          {p.kind === "box" ? <boxGeometry args={p.args} /> : <cylinderGeometry args={p.args} />}
          <meshStandardMaterial color={p.color} metalness={p.metal ?? 0.2} roughness={0.6} />
        </mesh>
      ))}
    </group>
  );
}
