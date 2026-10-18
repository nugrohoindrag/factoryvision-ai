/**
 * The molding hall around the injection machines: two rows of machine cells facing a central transfer conveyor,
 * the resin dryer and raw material at the back, and the material pipes from the dryer to every hopper.
 *
 * Geometry is plain data (hallLayout) so the layout can be tested without WebGL; the components below only draw it.
 * Machines themselves are drawn by the plant scene, at the positions in MOLDING_CELLS.
 */

import { useFrame } from "@react-three/fiber";
import { useMemo, useRef } from "react";
import * as THREE from "three";

type Vec3 = [number, number, number];

/** Hall centre line (the conveyor) and extent, in scene units (1 unit ≈ 1 m). */
export const HALL = { x: -24, z0: -23, z1: -3.2, halfWidth: 14.5 };
export const ROW_Z = [-7, -11.5, -16];
const MACHINE_X = 6.5; // machine centre distance from the conveyor
const TABLE_X = 2.6; // output table centre distance from the conveyor
const CELL_HALF_D = 1.75;

/** One cell per IMM: left row 01..03 front to back, right row 04..06 front to back, mold side towards the conveyor. */
export const MOLDING_CELLS: Record<string, { x: number; z: number; side: -1 | 1 }> = Object.fromEntries(
  ["IMM-01", "IMM-02", "IMM-03", "IMM-04", "IMM-05", "IMM-06"].map((id, i) => {
    const side = i < 3 ? -1 : 1;
    return [id, { x: HALL.x + side * MACHINE_X, z: ROW_Z[i % 3], side }];
  }),
) as Record<string, { x: number; z: number; side: -1 | 1 }>;
export const DRYER_POS: Vec3 = [HALL.x, 0, -20.5];

export type Strip = { pos: Vec3; size: [number, number] };

/** Yellow floor markings: an outline round each cell and round the walkway of the hall. */
export function floorMarkings(): Strip[] {
  const out: Strip[] = [];
  const rect = (cx: number, cz: number, w: number, d: number) => {
    out.push({ pos: [cx, 0, cz - d / 2], size: [w, 0.09] }, { pos: [cx, 0, cz + d / 2], size: [w, 0.09] });
    out.push({ pos: [cx - w / 2, 0, cz], size: [0.09, d] }, { pos: [cx + w / 2, 0, cz], size: [0.09, d] });
  };
  for (const c of Object.values(MOLDING_CELLS)) {
    const inner = HALL.x + c.side * (TABLE_X - 1.1);
    const outer = HALL.x + c.side * (MACHINE_X + 3);
    rect((inner + outer) / 2, c.z, Math.abs(outer - inner), 2 * CELL_HALF_D + 0.5);
  }
  rect(HALL.x, (HALL.z0 + HALL.z1) / 2 + 0.6, 2 * HALL.halfWidth - 1, HALL.z1 - HALL.z0 - 1.2);
  return out;
}

/** Fence posts and rails on the three sides of a cell that do not face the conveyor. */
export function fenceSegments(): { a: [number, number]; b: [number, number] }[] {
  const segs: { a: [number, number]; b: [number, number] }[] = [];
  for (const c of Object.values(MOLDING_CELLS)) {
    const inner = HALL.x + c.side * (TABLE_X - 0.9);
    const outer = HALL.x + c.side * (MACHINE_X + 2.8);
    const back = c.z - CELL_HALF_D;
    const front = c.z + CELL_HALF_D;
    segs.push({ a: [inner, back], b: [outer, back] }, { a: [outer, back], b: [outer, front] }, { a: [outer, front], b: [inner + c.side * 1.6, front] });
  }
  return segs;
}

/** Hopper top of a machine cell in world space, where its material pipe ends. */
export function hopperTop(id: string, w: number, h: number): Vec3 {
  const c = MOLDING_CELLS[id];
  return [c.x + c.side * 0.3 * w, 1.05 * h, c.z]; // injection side, away from the conveyor
}

const YELLOW = "#f2c200";

function FloorMarkings() {
  const strips = useMemo(floorMarkings, []);
  return (
    <group>
      {strips.map((s, i) => (
        <mesh key={i} rotation={[-Math.PI / 2, 0, 0]} position={[s.pos[0], 0.012, s.pos[2]]}>
          <planeGeometry args={s.size} />
          <meshBasicMaterial color={YELLOW} toneMapped={false} />
        </mesh>
      ))}
    </group>
  );
}

function Fences() {
  const segs = useMemo(fenceSegments, []);
  return (
    <group>
      {segs.map(({ a, b }, i) => {
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        const angle = -Math.atan2(b[1] - a[1], b[0] - a[0]);
        const mid: Vec3 = [(a[0] + b[0]) / 2, 0, (a[1] + b[1]) / 2];
        const posts = Math.max(2, Math.round(len / 1.2) + 1);
        return (
          <group key={i} position={mid} rotation={[0, angle, 0]}>
            {Array.from({ length: posts }, (_, k) => (
              <mesh key={k} position={[-len / 2 + (k * len) / (posts - 1), 0.65, 0]}>
                <boxGeometry args={[0.07, 1.3, 0.07]} />
                <meshStandardMaterial color={YELLOW} roughness={0.5} />
              </mesh>
            ))}
            {[0.25, 1.25].map((y) => (
              <mesh key={y} position={[0, y, 0]}>
                <boxGeometry args={[len, 0.05, 0.05]} />
                <meshStandardMaterial color={YELLOW} roughness={0.5} />
              </mesh>
            ))}
            <mesh position={[0, 0.75, 0]}>
              <planeGeometry args={[len, 0.95]} />
              <meshStandardMaterial color="#c9a400" transparent opacity={0.18} side={THREE.DoubleSide} depthWrite={false} />
            </mesh>
          </group>
        );
      })}
    </group>
  );
}

/** Green-topped output table between a machine and the conveyor, where the robot puts the molded bumper. */
function OutputTable({ x, z }: { x: number; z: number }) {
  return (
    <group position={[x, 0, z]}>
      <mesh position={[0, 0.82, 0]}>
        <boxGeometry args={[1.9, 0.08, 1.1]} />
        <meshStandardMaterial color="#1f8a4c" roughness={0.6} />
      </mesh>
      {[-0.85, 0.85].flatMap((dx) =>
        [-0.45, 0.45].map((dz) => (
          <mesh key={`${dx}${dz}`} position={[dx, 0.4, dz]}>
            <boxGeometry args={[0.06, 0.8, 0.06]} />
            <meshStandardMaterial color="#64748b" />
          </mesh>
        )),
      )}
    </group>
  );
}

const CRATE = new THREE.BoxGeometry(0.75, 0.35, 0.75);
const CRATES = 9;

/** Central transfer conveyor running from the back of the hall to the front, with crates of molded bumpers on it. */
function CentralConveyor() {
  const ref = useRef<THREE.InstancedMesh>(null);
  const z0 = HALL.z0 + 3.2;
  const len = HALL.z1 - z0;
  const m = useMemo(() => new THREE.Matrix4(), []);
  useFrame(({ clock }) => {
    const mesh = ref.current;
    if (!mesh) return;
    for (let i = 0; i < CRATES; i++) {
      const t = ((clock.elapsedTime * 0.35 + (i * len) / CRATES) % len) + z0;
      m.setPosition(HALL.x, 0.98, t);
      mesh.setMatrixAt(i, m);
    }
    mesh.instanceMatrix.needsUpdate = true;
  });
  return (
    <group>
      <mesh position={[HALL.x, 0.4, z0 + len / 2]}>
        <boxGeometry args={[1.3, 0.8, len]} />
        <meshStandardMaterial color="#9aa5b1" metalness={0.4} roughness={0.45} />
      </mesh>
      <mesh position={[HALL.x, 0.81, z0 + len / 2]}>
        <boxGeometry args={[1.1, 0.02, len]} />
        <meshStandardMaterial color="#2b3440" roughness={0.8} />
      </mesh>
      {[-0.68, 0.68].map((dx) => (
        <mesh key={dx} position={[HALL.x + dx, 0.88, z0 + len / 2]}>
          <boxGeometry args={[0.06, 0.16, len]} />
          <meshStandardMaterial color="#cbd5e1" metalness={0.6} roughness={0.3} />
        </mesh>
      ))}
      <instancedMesh ref={ref} args={[CRATE, undefined, CRATES]}>
        <meshStandardMaterial color="#1e293b" roughness={0.7} />
      </instancedMesh>
    </group>
  );
}

/** Resin sacks on pallets beside the dryer. */
function RawMaterial() {
  const stacks: Vec3[] = [-5.5, -3.6, 3.6, 5.5].map((dx) => [HALL.x + dx, 0, DRYER_POS[2] - 0.4]);
  return (
    <group>
      {stacks.map((p, i) => (
        <group key={i} position={p}>
          <mesh position={[0, 0.07, 0]}>
            <boxGeometry args={[1.5, 0.14, 1.2]} />
            <meshStandardMaterial color="#a47148" roughness={0.9} />
          </mesh>
          {[0, 1, 2].flatMap((row) =>
            [-0.37, 0.37].flatMap((dx) =>
              [-0.28, 0.28].map((dz) => (
                <mesh key={`${row}${dx}${dz}`} position={[dx, 0.27 + row * 0.27, dz]}>
                  <boxGeometry args={[0.7, 0.25, 0.52]} />
                  <meshStandardMaterial color="#efe9dc" roughness={0.95} />
                </mesh>
              )),
            ),
          )}
        </group>
      ))}
    </group>
  );
}

/** The resin line: a header over the conveyor from the dryer, and a branch down to each hopper. */
function MaterialPipes({ hoppers }: { hoppers: Vec3[] }) {
  const y = 3.7;
  const pipe = (from: Vec3, to: Vec3, key: string) => {
    const a = new THREE.Vector3(...from);
    const b = new THREE.Vector3(...to);
    const mid = a.clone().add(b).multiplyScalar(0.5);
    const dir = b.clone().sub(a);
    const q = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), dir.clone().normalize());
    return (
      <mesh key={key} position={mid.toArray() as Vec3} quaternion={q}>
        <cylinderGeometry args={[0.07, 0.07, dir.length(), 10]} />
        <meshStandardMaterial color="#d7dde3" metalness={0.7} roughness={0.3} />
      </mesh>
    );
  };
  const frontZ = Math.max(...hoppers.map((h) => h[2]));
  return (
    <group>
      {pipe([HALL.x, 3.1, DRYER_POS[2]], [HALL.x, y, DRYER_POS[2]], "riser")}
      {pipe([HALL.x, y, DRYER_POS[2]], [HALL.x, y, frontZ], "header")}
      {hoppers.flatMap((h, i) => [pipe([HALL.x, y, h[2]], [h[0], y, h[2]], `b${i}`), pipe([h[0], y, h[2]], [h[0], h[1] + 0.05, h[2]], `d${i}`)])}
    </group>
  );
}

/** Floor, walls, markings, fences, tables, conveyor, raw material and pipes of the molding hall. */
export default function MoldingHall({ hoppers }: { hoppers: Vec3[] }) {
  const depth = HALL.z1 - HALL.z0;
  const midZ = (HALL.z0 + HALL.z1) / 2;
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[HALL.x, 0.004, midZ]}>
        <planeGeometry args={[2 * HALL.halfWidth, depth]} />
        <meshStandardMaterial color="#c9d1d9" metalness={0.15} roughness={0.35} />
      </mesh>
      <mesh position={[HALL.x, 0.75, HALL.z0]}>
        <boxGeometry args={[2 * HALL.halfWidth, 1.5, 0.25]} />
        <meshStandardMaterial color="#e2e8f0" roughness={0.8} />
      </mesh>
      {[-1, 1].map((s) => (
        <mesh key={s} position={[HALL.x + s * HALL.halfWidth, 0.5, midZ]}>
          <boxGeometry args={[0.25, 1, depth]} />
          <meshStandardMaterial color="#e2e8f0" roughness={0.8} />
        </mesh>
      ))}
      <FloorMarkings />
      <Fences />
      {Object.values(MOLDING_CELLS).map((c) => (
        <OutputTable key={`${c.x}${c.z}`} x={HALL.x + c.side * TABLE_X} z={c.z} />
      ))}
      <CentralConveyor />
      <RawMaterial />
      <MaterialPipes hoppers={hoppers} />
    </group>
  );
}
