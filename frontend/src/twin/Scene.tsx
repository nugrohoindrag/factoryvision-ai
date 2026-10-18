/** Low-poly Vidad plant (TW-1..TW-3): 16 machines with andon lights and ID boards, WIP racks, warehouse, INS-01 conveyor.

Built from primitives and canvas-texture labels, plus one product image for the injection molding machines
(the dryer, hopper, IMM and robot cell line), and laid out so the virtual cameras see large andons, readable
IDs and countable racks.
*/

import { useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef } from "react";
import * as THREE from "three";
import type { ConveyorItem, Machine, Snapshot } from "../lib/types";
import { LINE_TOP, LineAsset } from "./LineAsset";

export const ANDON_HEX: Record<string, string> = { green: "#22c55e", yellow: "#facc15", red: "#ef4444", blue: "#3b82f6", grey: "#9ca3af" };
const COLOR_HEX: Record<string, string> = {
  C01: "#f2f2f0", C02: "#e8e6df", C04: "#b9bcc0", C05: "#1d1d1f", C06: "#7a1f24", C07: "#5b6066", C08: "#2b3a57",
  C09: "#8a8f94", C10: "#3d4f3a", C11: "#a1251e", C12: "#202325", C16: "#c9a227", C17: "#2f6db3",
};
export const SKUS = ["FB-ARKA", "RB-ARKA", "FB-BROMO", "RB-BROMO", "FB-CITRA", "RB-CITRA"];

type Pos = [number, number, number];
export const LAYOUT: Record<string, { pos: Pos; size: Pos }> = {
  "DRY-01": { pos: [-39, 0, -8], size: [2.4, 3, 2.4] },
  "IMM-01": { pos: [-34, 0, -8], size: [3.6, 2.2, 2.2] },
  "IMM-02": { pos: [-29, 0, -8], size: [3.6, 2.2, 2.2] },
  "IMM-03": { pos: [-24, 0, -8], size: [3.8, 2.4, 2.3] },
  "IMM-04": { pos: [-19, 0, -8], size: [4.0, 2.6, 2.4] },
  "IMM-05": { pos: [-14, 0, -8], size: [4.0, 2.6, 2.4] },
  "IMM-06": { pos: [-9, 0, -8], size: [3.8, 2.4, 2.3] },
  "TRM-01": { pos: [-3, 0, -8], size: [2.4, 1.8, 2] },
  "PWS-01": { pos: [1, 0, -8], size: [3, 2.2, 2.2] },
  "PNT-01": { pos: [6, 0, -8], size: [3.4, 3, 3] },
  "PNT-02": { pos: [11, 0, -8], size: [3.4, 3, 3] },
  "OVN-01": { pos: [17, 0, -8], size: [5, 2.6, 3] },
  "ASM-01": { pos: [23, 0, -8], size: [2.4, 1.6, 2] },
  "ASM-02": { pos: [27, 0, -8], size: [2.4, 1.6, 2] },
  "ASM-03": { pos: [31, 0, -8], size: [2.4, 1.6, 2] },
  "INS-01": { pos: [36, 0, -8], size: [2.6, 1.8, 2] },
};
export const WIP_RACK_X = SKUS.map((_, k) => -32 + 4 * k);
export const WH_RACK_X = SKUS.map((_, k) => 14 + 4 * k);
export const CONVEYOR = { x0: 31, x1: 41, z: -2.5, y: 0.9 };

/** Text on a canvas texture; big and high-contrast so the cameras (and Gemini) can read it. */
function useLabel(lines: string[], w = 256, h = 128, bg = "#0b1220", fg = "#ffffff") {
  const tex = useMemo(() => {
    const c = document.createElement("canvas");
    c.width = w;
    c.height = h;
    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
  }, [w, h]);
  const key = lines.join("|");
  useEffect(() => {
    const c = tex.image as HTMLCanvasElement;
    const g = c.getContext("2d")!;
    g.fillStyle = bg;
    g.fillRect(0, 0, w, h);
    g.fillStyle = fg;
    g.textAlign = "center";
    g.textBaseline = "middle";
    lines.forEach((l, i) => {
      g.font = `bold ${i === 0 ? Math.round(h * 0.42) : Math.round(h * 0.26)}px Arial, sans-serif`;
      g.fillText(l, w / 2, lines.length === 1 ? h / 2 : h * (0.32 + 0.42 * i));
    });
    tex.needsUpdate = true;
  }, [key, tex, w, h, bg, fg]); // eslint-disable-line react-hooks/exhaustive-deps
  return tex;
}

function Board({ lines, position, width = 2.6, height = 1.1 }: { lines: string[]; position: Pos; width?: number; height?: number }) {
  const tex = useLabel(lines);
  return (
    <mesh position={position}>
      <planeGeometry args={[width, height]} />
      <meshBasicMaterial map={tex} toneMapped={false} />
    </mesh>
  );
}

function MachineMesh({ m }: { m: Machine }) {
  const l = LAYOUT[m.machine_id];
  const [x, , z] = l.pos;
  const [w, h, d] = l.size;
  const imm = m.machine_id.startsWith("IMM");
  // The andon, mast and ID board sit on the top of the machine (for an IMM, the top of the robot arm).
  const top = imm ? LINE_TOP * h : h;
  const modelled = imm || m.machine_id === "DRY-01";
  const andon = ANDON_HEX[m.andon];
  const perf = m.performance_pct != null ? `${Math.round(m.performance_pct)}%` : m.status.toUpperCase();
  return (
    <group position={[x, 0, z]}>
      {modelled ? (
        <LineAsset kind={imm ? "imm" : "dryer"} w={w} h={h} d={d} />
      ) : (
        <mesh position={[0, h / 2, 0]} castShadow>
          <boxGeometry args={[w, h, d]} />
          <meshStandardMaterial color="#7b8794" metalness={0.2} roughness={0.7} />
        </mesh>
      )}
      <mesh position={[w / 2 - 0.3, top + 0.6, 0]}>
        <cylinderGeometry args={[0.06, 0.06, 1.2]} />
        <meshStandardMaterial color="#333" />
      </mesh>
      <mesh position={[w / 2 - 0.3, top + 1.35, 0]}>
        <sphereGeometry args={[imm ? 0.5 : 0.38, 20, 16]} />
        <meshStandardMaterial color={andon} emissive={andon} emissiveIntensity={1.6} toneMapped={false} />
      </mesh>
      <Board lines={imm ? [m.machine_id, perf] : [m.machine_id]} position={[-0.2, top + 0.75, d / 2 + 0.02]} width={imm ? 2.6 : 2} height={imm ? 1.2 : 0.8} />
    </group>
  );
}

const BUMPER = new THREE.BoxGeometry(0.5, 0.18, 0.16);

/** A rack whose bumper count is the stock (TW-3); one instanced mesh per rack. */
function Rack({ x, z, label, count, color, hold = false, perRow = 5, rows = 8 }: { x: number; z: number; label: string; count: number; color: string; hold?: boolean; perRow?: number; rows?: number }) {
  const ref = useRef<THREE.InstancedMesh>(null);
  const max = perRow * rows;
  const n = Math.max(0, Math.min(max, count));
  useEffect(() => {
    const mesh = ref.current;
    if (!mesh) return;
    const m = new THREE.Matrix4();
    for (let i = 0; i < n; i++) {
      const c = i % perRow;
      const r = Math.floor(i / perRow);
      m.setPosition((c - (perRow - 1) / 2) * 0.58, 0.35 + r * 0.27, 0);
      mesh.setMatrixAt(i, m);
    }
    mesh.count = n;
    mesh.instanceMatrix.needsUpdate = true;
  }, [n, perRow]);
  return (
    <group position={[x, 0, z]}>
      {[-1, 1].map((s) => (
        <mesh key={s} position={[s * (perRow * 0.29 + 0.1), (rows * 0.27 + 0.4) / 2, 0]}>
          <boxGeometry args={[0.08, rows * 0.27 + 0.4, 0.5]} />
          <meshStandardMaterial color={hold ? "#f97316" : "#475569"} />
        </mesh>
      ))}
      <instancedMesh ref={ref} args={[BUMPER, undefined, max]} key={max}>
        <meshStandardMaterial color={hold ? "#f97316" : color} roughness={0.5} />
      </instancedMesh>
      <Board lines={[label]} position={[0, rows * 0.27 + 0.95, 0.3]} width={2.8} height={0.7} />
    </group>
  );
}

function Conveyor({ items, photos }: { items: ConveyorItem[]; photos: Record<string, string> }) {
  const group = useRef<THREE.Group>(null);
  const loader = useMemo(() => new THREE.TextureLoader(), []);
  const textures = useMemo(() => {
    const out: Record<string, THREE.Texture> = {};
    for (const it of items) {
      const f = it.photo_id ? photos[it.photo_id] : undefined;
      if (f && !out[it.photo_id!]) {
        const t = loader.load(`/api/photos/${f}`);
        t.colorSpace = THREE.SRGBColorSpace;
        out[it.photo_id!] = t;
      }
    }
    return out;
  }, [items.map((i) => i.photo_id).join(","), photos, loader]); // eslint-disable-line react-hooks/exhaustive-deps
  // Smooth motion between ticks: drift right, snap back when the next snapshot arrives.
  useFrame((_, dt) => {
    if (group.current) group.current.position.x = Math.min(group.current.position.x + dt * 0.25, 1.6);
  });
  useEffect(() => {
    if (group.current) group.current.position.x = 0;
  }, [items[0]?.seq]); // eslint-disable-line react-hooks/exhaustive-deps
  const span = CONVEYOR.x1 - CONVEYOR.x0;
  return (
    <group>
      <mesh position={[(CONVEYOR.x0 + CONVEYOR.x1) / 2, CONVEYOR.y / 2, CONVEYOR.z]}>
        <boxGeometry args={[span, CONVEYOR.y, 1.6]} />
        <meshStandardMaterial color="#334155" />
      </mesh>
      <group ref={group}>
        {items.map((it, k) => {
          const x = CONVEYOR.x1 - 0.9 - k * 1.7;
          const tex = it.photo_id ? textures[it.photo_id] : undefined;
          return (
            <mesh key={it.seq} position={[x, CONVEYOR.y + 0.01, CONVEYOR.z]} rotation={[-Math.PI / 2, 0, 0]}>
              <planeGeometry args={[1.5, 1.1]} />
              {tex ? <meshBasicMaterial map={tex} toneMapped={false} /> : <meshStandardMaterial color={COLOR_HEX[it.color] ?? "#ccc"} />}
            </mesh>
          );
        })}
      </group>
    </group>
  );
}

export default function PlantScene({ snap, photos }: { snap: Snapshot; photos: Record<string, string> }) {
  const machines = snap.machines.filter((m) => LAYOUT[m.machine_id]);
  return (
    <>
      <color attach="background" args={["#dfe6ec"]} />
      <hemisphereLight args={["#ffffff", "#8899aa", 1.1]} />
      <directionalLight position={[10, 25, 15]} intensity={1.6} />
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, -0.01, -2]}>
        <planeGeometry args={[90, 34]} />
        <meshStandardMaterial color="#b8c2cc" />
      </mesh>
      {[
        ["MOLDING", -22, "#d6dee6"],
        ["PAINTING", 11, "#e3dccf"],
        ["ASSEMBLY", 27, "#d9e3d6"],
      ].map(([label, x, c]) => (
        <mesh key={label as string} rotation={[-Math.PI / 2, 0, 0]} position={[x as number, 0, -8]}>
          <planeGeometry args={[label === "MOLDING" ? 38 : 14, 6]} />
          <meshStandardMaterial color={c as string} />
        </mesh>
      ))}
      {machines.map((m) => <MachineMesh key={m.machine_id} m={m} />)}
      {SKUS.map((sku, k) => (
        <Rack key={sku} x={WIP_RACK_X[k]} z={0} label={`WIP ${sku}`} count={snap.wip[sku] ?? 0} color="#1f2937" />
      ))}
      {SKUS.map((sku, k) => {
        const racks = snap.warehouse[sku] ?? [];
        const units = racks.reduce((a, r) => a + r.units, 0);
        const hold = racks.some((r) => r.on_hold);
        return <Rack key={sku} x={WH_RACK_X[k]} z={5} label={`SGL ${sku}`} count={Math.ceil(units / 10)} color={"#d1d5db"} hold={hold} perRow={5} rows={6} />;
      })}
      <Conveyor items={snap.conveyor} photos={photos} />
    </>
  );
}
