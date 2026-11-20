import { PerspectiveCamera, Vector3 } from "three";
import { describe, expect, it } from "vitest";

import { FOV_DEGREES, orbit, type Camera3D, type Vec3 } from "./camera3d";
import {
  clipPlanes,
  depthOf,
  intersectPlane,
  project,
  projectAll,
  screenRay,
  unproject,
  viewProjection,
} from "./projection";

const VIEWPORT = { width: 800, height: 600 };
const FRONT: Camera3D = { position: [0, 0, 500], target: [0, 0, 0], up: [0, 1, 0] };
const OBLIQUE = orbit({ position: [40, -20, 700], target: [40, -20, 30], up: [0, 1, 0] }, 0.7, 0.4);

/** three.js's own projection of `point`, for comparison. */
function threeProject(camera: Camera3D, point: Vec3) {
  const { near, far } = clipPlanes(500, 500);
  const three = new PerspectiveCamera(FOV_DEGREES, VIEWPORT.width / VIEWPORT.height, near, far);
  three.position.set(...camera.position);
  three.up.set(...camera.up);
  three.lookAt(...camera.target);
  three.updateMatrixWorld();
  const ndc = new Vector3(...point).project(three);
  return { x: (ndc.x * 0.5 + 0.5) * VIEWPORT.width, y: (0.5 - ndc.y * 0.5) * VIEWPORT.height };
}

describe("projection", () => {
  it("projects like three.js's PerspectiveCamera", () => {
    const { near, far } = clipPlanes(500, 500);
    for (const camera of [FRONT, OBLIQUE]) {
      const m = viewProjection(camera, VIEWPORT.width / VIEWPORT.height, near, far);
      for (const point of [
        [0, 0, 0],
        [120, -80, 40],
        [-200, 150, -90],
      ] as Vec3[]) {
        const ours = project(m, point, VIEWPORT);
        const theirs = threeProject(camera, point);
        expect(ours.x).toBeCloseTo(theirs.x, 3);
        expect(ours.y).toBeCloseTo(theirs.y, 3);
      }
    }
  });

  it("puts the target at the center, at its distance", () => {
    const m = viewProjection(FRONT, 4 / 3, 1, 5000);
    const center = project(m, [0, 0, 0], VIEWPORT);
    expect(center).toEqual({ x: 400, y: 300, depth: expect.closeTo(500, 6) as number });
    // Graph y up is screen y down.
    expect(project(m, [0, 10, 0], VIEWPORT).y).toBeLessThan(300);
    expect(project(m, [10, 0, 0], VIEWPORT).x).toBeGreaterThan(400);
  });

  it("marks points behind the camera with a depth ≤ 0", () => {
    const m = viewProjection(FRONT, 4 / 3, 1, 5000);
    expect(project(m, [0, 0, 600], VIEWPORT).depth).toBeLessThan(0);
  });

  it("projects many points at once like one at a time", () => {
    const m = viewProjection(OBLIQUE, 4 / 3, 1, 5000);
    const positions = Float32Array.from([0, 0, 0, 10, 20, 30, -50, 5, 80]);
    const out = new Float32Array(9);
    projectAll(m, positions, 3, VIEWPORT, out);
    for (let i = 0; i < 3; i += 1) {
      const one = project(
        m,
        [positions[i * 3] ?? 0, positions[i * 3 + 1] ?? 0, positions[i * 3 + 2] ?? 0],
        VIEWPORT,
      );
      expect(out[i * 3]).toBeCloseTo(one.x, 2);
      expect(out[i * 3 + 1]).toBeCloseTo(one.y, 2);
      expect(out[i * 3 + 2]).toBeCloseTo(one.depth, 2);
    }
  });

  it("round-trips: a projected point unprojects to itself on its plane", () => {
    for (const camera of [FRONT, OBLIQUE]) {
      const m = viewProjection(camera, 4 / 3, 1, 5000);
      for (const point of [
        [0, 0, 0],
        [130, -60, 25],
        [-75, 90, -140],
      ] as Vec3[]) {
        const screen = project(m, point, VIEWPORT);
        const back = unproject(camera, screen, VIEWPORT, point);
        back.forEach((value, axis) => {
          expect(value).toBeCloseTo(point[axis] ?? 0, 2);
        });
      }
    }
  });

  it("casts rays from the camera, through the center along the view direction", () => {
    const ray = screenRay(FRONT, { x: 400, y: 300 }, VIEWPORT);
    expect(ray.origin).toEqual([0, 0, 500]);
    expect(ray.direction[2]).toBeCloseTo(-1, 6);
    expect(depthOf(FRONT, [0, 0, 0])).toBeCloseTo(500, 6);
  });

  it("intersects planes, and misses parallel planes and planes behind", () => {
    const ray = { origin: [0, 0, 10] as Vec3, direction: [0, 0, -1] as Vec3 };
    expect(intersectPlane(ray, [0, 0, 0], [0, 0, 1])).toEqual([0, 0, 0]);
    expect(intersectPlane(ray, [0, 0, 0], [1, 0, 0])).toBeNull();
    expect(intersectPlane(ray, [0, 0, 20], [0, 0, 1])).toBeNull();
    // A ray that misses its plane falls back to the target.
    const sideways: Camera3D = { position: [0, 0, 10], target: [0, 0, 0], up: [0, 1, 0] };
    expect(unproject(sideways, { x: 400, y: 300 }, VIEWPORT, [0, 0, 50])).toEqual([0, 0, 0]);
  });

  it("chooses clip planes around the scene", () => {
    const { near, far } = clipPlanes(1000, 400);
    expect(near).toBeCloseTo(1, 6);
    expect(far).toBeGreaterThan(1000 + 400);
    expect(clipPlanes(1, 1).near).toBe(0.05);
  });
});
