import { describe, expect, it } from "vitest";

import {
  basisOf,
  boundedCamera,
  camera2dTo3d,
  camera3dTo2d,
  boundingSphere,
  distanceToShow,
  distanceOf,
  dolly,
  dollyToward,
  easeInOut,
  flyTo,
  frameSphere,
  frameSphereInArea,
  inView2d,
  interpolateCamera,
  isCamera3D,
  MAX_COORDINATE,
  MIN_DISTANCE,
  moveTarget,
  normalize,
  orbit,
  pan,
  POLAR_MARGIN,
  rotateAround,
  worldPerPixel,
  type Camera3D,
  type Vec3,
} from "./camera3d";
import { project, viewProjection } from "./projection";

const FRONT: Camera3D = { position: [0, 0, 100], target: [0, 0, 0], up: [0, 1, 0] };
const VIEWPORT = { width: 800, height: 600 };

function expectVec(actual: Vec3, expected: Vec3, digits = 6) {
  actual.forEach((value, i) => {
    expect(value).toBeCloseTo(expected[i] ?? 0, digits);
  });
}

describe("camera moves", () => {
  it("rotates vectors around an axis (right-handed)", () => {
    expectVec(rotateAround([1, 0, 0], [0, 0, 1], Math.PI / 2), [0, 1, 0]);
    expectVec(normalize([0, 0, 0], [1, 0, 0]), [1, 0, 0]);
  });

  it("has a right-handed basis: right is +x, up is +y from the front", () => {
    const { forward, right, up } = basisOf(FRONT);
    expectVec(forward, [0, 0, -1]);
    expectVec(right, [1, 0, 0]);
    expectVec(up, [0, 1, 0]);
    // A camera looking straight along its up vector still gets a basis.
    expect(basisOf({ position: [0, 10, 0], target: [0, 0, 0], up: [0, 1, 0] }).right).toHaveLength(
      3,
    );
  });

  it("orbits around the target, keeping the distance", () => {
    const right = orbit(FRONT, Math.PI / 2, 0);
    expectVec(right.position, [100, 0, 0]);
    expect(distanceOf(right)).toBeCloseTo(100, 6);
    const above = orbit(FRONT, 0, Math.PI / 4);
    expect(above.position[1]).toBeGreaterThan(0);
    expect(distanceOf(above)).toBeCloseTo(100, 6);
  });

  it("stops short of the poles", () => {
    const top = orbit(FRONT, 0, Math.PI);
    const angle = Math.acos(top.position[1] / distanceOf(top));
    expect(angle).toBeCloseTo(POLAR_MARGIN, 6);
    const bottom = orbit(FRONT, 0, -Math.PI);
    expect(Math.acos(bottom.position[1] / distanceOf(bottom))).toBeCloseTo(
      Math.PI - POLAR_MARGIN,
      6,
    );
  });

  it("pans in screen directions, by pixels at the target's distance", () => {
    const moved = pan(FRONT, 10, 0, VIEWPORT.height);
    const unit = worldPerPixel(100, VIEWPORT.height);
    expectVec(moved.target, [10 * unit, 0, 0]);
    expectVec(moved.position, [10 * unit, 0, 100]);
    // Screen y points down: panning down shows what is below.
    expect(pan(FRONT, 0, 10, VIEWPORT.height).target[1]).toBeLessThan(0);
  });

  it("zooms toward the target within bounds", () => {
    expect(distanceOf(dolly(FRONT, 0.5))).toBeCloseTo(50, 6);
    expect(distanceOf(dolly(FRONT, 1e-9))).toBeCloseTo(MIN_DISTANCE, 6);
  });

  it("zooms toward a point that stays put on the screen", () => {
    const point: Vec3 = [30, -20, 0];
    const before = project(viewProjection(FRONT, 4 / 3, 0.1, 1000), point, VIEWPORT);
    const zoomed = dollyToward(FRONT, point, 0.5);
    const after = project(viewProjection(zoomed, 4 / 3, 0.1, 1000), point, VIEWPORT);
    expect(after.x).toBeCloseTo(before.x, 4);
    expect(after.y).toBeCloseTo(before.y, 4);
    expect(distanceOf(zoomed)).toBeCloseTo(50, 6);
  });

  it("moves the target with the camera", () => {
    const moved = moveTarget(FRONT, [5, 6, 7]);
    expectVec(moved.position, [5, 6, 107]);
  });
});

describe("framing and animation", () => {
  it("frames a sphere so all of it is on screen", () => {
    const sphere = boundingSphere(Float32Array.from([-100, -50, 0, 100, 50, 0, 0, 0, 40]), 3);
    expectVec(sphere.center, [0, 0, 20]);
    const camera = frameSphere(sphere, 4 / 3, [0, 0, 1]);
    const m = viewProjection(camera, 4 / 3, 0.1, 10_000);
    for (const corner of [
      [-100, -50, 0],
      [100, 50, 0],
    ] as Vec3[]) {
      const p = project(m, corner, VIEWPORT);
      expect(p.x).toBeGreaterThanOrEqual(0);
      expect(p.x).toBeLessThanOrEqual(800);
      expect(p.y).toBeGreaterThanOrEqual(0);
      expect(p.y).toBeLessThanOrEqual(600);
    }
    expect(boundingSphere([], 0)).toEqual({ center: [0, 0, 0], radius: 1 });
  });

  it("flies to a point, closer but never further", () => {
    const near = flyTo(FRONT, [10, 10, 0], 40);
    expectVec(near.target, [10, 10, 0]);
    expect(distanceOf(near)).toBeCloseTo(40, 6);
    expect(distanceOf(flyTo(FRONT, [0, 0, 0], 5000))).toBeCloseTo(100, 6);
  });

  it("interpolates between cameras, ending exactly at both", () => {
    const to: Camera3D = { position: [50, 400, 0], target: [50, 0, 0], up: [0, 0, -1] };
    expect(interpolateCamera(FRONT, to, 0)).toBe(FRONT);
    expect(interpolateCamera(FRONT, to, 1)).toBe(to);
    const middle = interpolateCamera(FRONT, to, 0.5);
    expectVec(middle.target, [25, 0, 0]);
    expect(distanceOf(middle)).toBeCloseTo(200, 3);
    expect(easeInOut(0)).toBe(0);
    expect(easeInOut(1)).toBe(1);
    expect(easeInOut(0.5)).toBeCloseTo(0.5, 6);
  });
});

describe("validity", () => {
  it("accepts usable cameras only", () => {
    expect(isCamera3D(FRONT)).toBe(true);
    expect(isCamera3D(null)).toBe(false);
    expect(isCamera3D({ ...FRONT, up: [0, 0, 0] })).toBe(false);
    expect(isCamera3D({ ...FRONT, up: [0, 2, 0] })).toBe(false);
    expect(isCamera3D({ ...FRONT, position: [0, 0, 0] })).toBe(false);
    expect(isCamera3D({ ...FRONT, target: [0, Number.NaN, 0] })).toBe(false);
    expect(isCamera3D({ ...FRONT, target: [0, MAX_COORDINATE * 2, 0] })).toBe(false);
    expect(isCamera3D({ ...FRONT, target: [0, 0] })).toBe(false);
  });

  it("bounds cameras for saving", () => {
    const bounded = boundedCamera({
      position: [Infinity, 2e6, 0],
      target: [0, 1e6, 0],
      up: [0, 3, 0],
    });
    expect(bounded.position).toEqual([0, 1e6, 0]);
    expect(isCamera3D(bounded)).toBe(true);
    expectVec(bounded.up, [0, 1, 0]);
  });
});

describe("keeping points in view", () => {
  it("finds the distance that shows every point, closer points needing more", () => {
    const points = Float32Array.from([100, 0, 0, 0, 50, 0, 100, 0, 50]);
    const flat = distanceToShow(FRONT, points, [0, 1], 4 / 3, 1);
    // At that distance the farther-out point sits exactly on the screen's edge.
    const m = viewProjection({ ...FRONT, position: [0, 0, flat] }, 4 / 3, 0.1, 1000);
    const edge = Math.max(
      project(m, [100, 0, 0], VIEWPORT).x,
      800 - project(m, [100, 0, 0], VIEWPORT).x,
    );
    expect(edge).toBeCloseTo(800, 1);
    // The same point 50 units nearer the camera needs 50 more.
    expect(distanceToShow(FRONT, points, [2], 4 / 3, 1)).toBeCloseTo(
      distanceToShow(FRONT, points, [0], 4 / 3, 1) + 50,
      4,
    );
    expect(distanceToShow(FRONT, points, [], 4 / 3)).toBe(0);
  });

  it("tells what a 2D camera shows, rotation included", () => {
    const camera = { x: 10, y: 0, ratio: 1, angle: 0 };
    expect(inView2d(camera, 10 + 399, 0, 800, 600)).toBe(true);
    expect(inView2d(camera, 10 + 401, 0, 800, 600)).toBe(false);
    expect(inView2d(camera, 10, 299, 800, 600)).toBe(true);
    const turned = { ...camera, angle: Math.PI / 2 };
    expect(inView2d(turned, 10 + 350, 0, 800, 600)).toBe(false);
    expect(inView2d(turned, 10 + 290, 0, 800, 600)).toBe(true);
  });
});

describe("2D ↔ 3D cameras", () => {
  it("looks at the same area, at the same scale and rotation", () => {
    const camera = { x: 12, y: -30, ratio: 0.8, angle: 0.4 };
    const three = camera2dTo3d(camera, 600);
    expectVec(three.target, [12, -30, 0]);
    // One pixel at the target covers `ratio` graph units.
    expect(worldPerPixel(distanceOf(three), 600)).toBeCloseTo(0.8, 6);
    const back = camera3dTo2d(three, 600);
    expect(back.x).toBeCloseTo(12, 6);
    expect(back.y).toBeCloseTo(-30, 6);
    expect(back.ratio).toBeCloseTo(0.8, 6);
    expect(back.angle).toBeCloseTo(0.4, 6);
  });

  it("maps an orbited camera to its target and scale, unrotated", () => {
    const orbited = orbit(camera2dTo3d({ x: 0, y: 0, ratio: 1, angle: 0 }, 600), 1, 0.3);
    const flat = camera3dTo2d(orbited, 600);
    expect(flat.angle).toBeCloseTo(0, 6);
    expect(flat.ratio).toBeCloseTo(1, 6);
    expect(camera3dTo2d({ ...orbited, up: [0, 0, 1] }, 600).angle).toBe(0);
  });
});

describe("frameSphereInArea", () => {
  const sphere = { center: [100, 50, 20] as Vec3, radius: 300 };
  const W = 1000;
  const H = 700;

  /** Screen points of the sphere's center and of many points on its surface. */
  function shown(camera: Camera3D) {
    const m = viewProjection(camera, W / H, 1, 1e5);
    const viewport = { width: W, height: H };
    const surface = [];
    for (let i = 0; i < 24; i += 1) {
      for (let j = 1; j < 12; j += 1) {
        const theta = (i / 24) * 2 * Math.PI;
        const phi = (j / 12) * Math.PI;
        const offset: Vec3 = [
          Math.sin(phi) * Math.cos(theta),
          Math.cos(phi),
          Math.sin(phi) * Math.sin(theta),
        ];
        surface.push(
          project(
            m,
            [
              sphere.center[0] + offset[0] * sphere.radius,
              sphere.center[1] + offset[1] * sphere.radius,
              sphere.center[2] + offset[2] * sphere.radius,
            ],
            viewport,
          ),
        );
      }
    }
    return { center: project(m, sphere.center, viewport), surface };
  }

  it("is frameSphere when nothing is covered", () => {
    const plain = frameSphere(sphere, W / H);
    const area = frameSphereInArea(sphere, W, H);
    for (let k = 0; k < 3; k += 1) {
      expect(area.position[k]).toBeCloseTo(plain.position[k] ?? 0, 6);
      expect(area.target[k]).toBeCloseTo(plain.target[k] ?? 0, 6);
    }
  });

  it("centers the sphere in the uncovered area and keeps all of it there", () => {
    const insets = { top: 0, right: 0, bottom: 140, left: 72 };
    const { center, surface } = shown(frameSphereInArea(sphere, W, H, insets));
    expect(center.x).toBeCloseTo(72 + (W - 72) / 2, 3);
    expect(center.y).toBeCloseTo((H - 140) / 2, 3);
    for (const point of surface) {
      expect(point.x).toBeGreaterThanOrEqual(72);
      expect(point.x).toBeLessThanOrEqual(W);
      expect(point.y).toBeGreaterThanOrEqual(0);
      expect(point.y).toBeLessThanOrEqual(H - 140);
    }
  });

  it("ignores insets that would leave too little room", () => {
    const covered = frameSphereInArea(sphere, W, H, { top: 0, right: 0, bottom: 600, left: 0 });
    const plain = frameSphere(sphere, W / H);
    expect(distanceOf(covered)).toBeCloseTo(distanceOf(plain), 6);
  });
});
