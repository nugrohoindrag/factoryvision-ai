import { HALL, MOLDING_CELLS, fenceSegments, floorMarkings, hopperTop } from "./MoldingHall";
import { LAYOUT } from "./Scene";

const IMMS = ["IMM-01", "IMM-02", "IMM-03", "IMM-04", "IMM-05", "IMM-06"];

it("lays the six injection machines out three a side of the conveyor, IMM-04 front right", () => {
  expect(IMMS.filter((id) => MOLDING_CELLS[id].x < HALL.x)).toEqual(["IMM-01", "IMM-02", "IMM-03"]);
  expect(IMMS.filter((id) => MOLDING_CELLS[id].x > HALL.x)).toEqual(["IMM-04", "IMM-05", "IMM-06"]);
  const right = IMMS.slice(3).map((id) => MOLDING_CELLS[id].z);
  expect(MOLDING_CELLS["IMM-04"].z).toBe(Math.max(...right)); // nearest the camera
});

it("keeps every machine inside the hall, clear of the conveyor and of its neighbours", () => {
  const boxes = IMMS.map((id) => {
    const [x, , z] = LAYOUT[id].pos;
    const [w, , d] = LAYOUT[id].size;
    return { id, x0: x - w / 2, x1: x + w / 2, z0: z - d / 2, z1: z + d / 2 };
  });
  for (const b of boxes) {
    expect(b.x0).toBeGreaterThan(HALL.x - HALL.halfWidth);
    expect(b.x1).toBeLessThan(HALL.x + HALL.halfWidth);
    expect(b.z0).toBeGreaterThan(HALL.z0);
    expect(b.z1).toBeLessThan(HALL.z1);
    expect(Math.min(Math.abs(b.x0 - HALL.x), Math.abs(b.x1 - HALL.x))).toBeGreaterThan(3); // room for table and conveyor
  }
  for (const a of boxes)
    for (const b of boxes) if (a !== b) expect(a.x1 <= b.x0 || b.x1 <= a.x0 || a.z1 <= b.z0 || b.z1 <= a.z0).toBe(true);
});

it("fences each cell without cutting through its machine", () => {
  for (const { a, b } of fenceSegments()) {
    for (const id of IMMS) {
      const [x, , z] = LAYOUT[id].pos;
      const [w, , d] = LAYOUT[id].size;
      const inside = (px: number, pz: number) => px > x - w / 2 && px < x + w / 2 && pz > z - d / 2 && pz < z + d / 2;
      for (let t = 0; t <= 1; t += 0.05) expect(inside(a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))).toBe(false);
    }
  }
  expect(floorMarkings().length).toBeGreaterThan(IMMS.length * 4);
});

it("runs a material pipe to each hopper on the injection side, away from the conveyor", () => {
  for (const id of IMMS) {
    const [hx] = hopperTop(id, LAYOUT[id].size[0], LAYOUT[id].size[1]);
    expect(Math.abs(hx - HALL.x)).toBeGreaterThan(Math.abs(MOLDING_CELLS[id].x - HALL.x));
  }
});
