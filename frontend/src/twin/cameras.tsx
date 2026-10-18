/** Virtual cameras (TW-7, TW-8, TW-14): offscreen renders of fixed views, frame diff in the browser, JPEG for the analyzer.

No timer ever calls Gemini. Thumbnails are rendered about once a second and compared with the
last analysed frame on a 64x36 grey grid; only a change above the threshold (or the
"Ask AI to look" button) captures a 1280x720 JPEG for POST /api/vision/frame.
*/

import { useThree } from "@react-three/fiber";
import { useEffect } from "react";
import * as THREE from "three";
import { CONVEYOR, WH_RACK_X, WIP_RACK_X } from "./Scene";

export type CameraId = "CAM-Molding" | "CAM-WIP" | "CAM-Warehouse" | "CAM-Inspection";

const mid = (xs: number[]) => (xs[0] + xs[xs.length - 1]) / 2;
export const CAMERAS: Record<CameraId, { pos: [number, number, number]; look: [number, number, number]; fov: number; label: string }> = {
  "CAM-Molding": { pos: [-21.5, 6, 9], look: [-21.5, 2.2, -8], fov: 46, label: "Molding IMM-01..06" },
  "CAM-WIP": { pos: [mid(WIP_RACK_X), 2.6, 15], look: [mid(WIP_RACK_X), 1.4, 0], fov: 40, label: "WIP racks" },
  "CAM-Warehouse": { pos: [mid(WH_RACK_X), 2.6, 19], look: [mid(WH_RACK_X), 1.2, 5], fov: 40, label: "Warehouse (SGL)" },
  "CAM-Inspection": { pos: [36, 5, 0.5], look: [36, CONVEYOR.y, CONVEYOR.z], fov: 55, label: "INS-01 conveyor" },
};

export type Capture = (id: CameraId, w: number, h: number) => HTMLCanvasElement;

const cams = new Map<CameraId, THREE.PerspectiveCamera>();
function camera(id: CameraId, aspect: number) {
  let c = cams.get(id);
  if (!c) {
    const d = CAMERAS[id];
    c = new THREE.PerspectiveCamera(d.fov, aspect, 0.1, 200);
    c.position.set(...d.pos);
    c.lookAt(new THREE.Vector3(...d.look));
    cams.set(id, c);
  }
  c.aspect = aspect;
  c.updateProjectionMatrix();
  return c;
}

/** Lives inside the Canvas and hands a capture function to the page. */
export function CameraRig({ onReady }: { onReady: (capture: Capture) => void }) {
  const { gl, scene } = useThree();
  useEffect(() => {
    const targets = new Map<string, THREE.WebGLRenderTarget>();
    const capture: Capture = (id, w, h) => {
      const key = `${w}x${h}`;
      let rt = targets.get(key);
      if (!rt) {
        rt = new THREE.WebGLRenderTarget(w, h);
        rt.texture.colorSpace = THREE.SRGBColorSpace;
        targets.set(key, rt);
      }
      const prev = gl.getRenderTarget();
      gl.setRenderTarget(rt);
      gl.render(scene, camera(id, w / h));
      const buf = new Uint8Array(w * h * 4);
      gl.readRenderTargetPixels(rt, 0, 0, w, h, buf);
      gl.setRenderTarget(prev);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d")!;
      const img = ctx.createImageData(w, h);
      for (let y = 0; y < h; y++) img.data.set(buf.subarray((h - 1 - y) * w * 4, (h - y) * w * 4), y * w * 4); // flip Y
      ctx.putImageData(img, 0, 0);
      return canvas;
    };
    onReady(capture);
    return () => targets.forEach((t) => t.dispose());
  }, [gl, scene, onReady]);
  return null;
}

/** 64x36 grey signature of a frame. */
export function signature(canvas: HTMLCanvasElement): Float32Array {
  const s = document.createElement("canvas");
  s.width = 64;
  s.height = 36;
  const g = s.getContext("2d")!;
  g.drawImage(canvas, 0, 0, 64, 36);
  const d = g.getImageData(0, 0, 64, 36).data;
  const out = new Float32Array(64 * 36);
  for (let i = 0; i < out.length; i++) out[i] = (d[i * 4] * 0.299 + d[i * 4 + 1] * 0.587 + d[i * 4 + 2] * 0.114) / 255;
  return out;
}

/** Mean absolute difference between two signatures (0..1). */
export function frameDiff(a: Float32Array | null, b: Float32Array): number {
  if (!a) return 1;
  let s = 0;
  for (let i = 0; i < b.length; i++) s += Math.abs(a[i] - b[i]);
  return s / b.length;
}

export function toJpeg(canvas: HTMLCanvasElement): Promise<Blob> {
  return new Promise((res, rej) => canvas.toBlob((b) => (b ? res(b) : rej(new Error("capture failed"))), "image/jpeg", 0.85));
}
