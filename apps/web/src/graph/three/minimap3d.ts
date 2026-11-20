/**
 * The 3D minimap: the graph from the top (its x / y plane, the 2D
 * picture), every visible node as a dot, and the camera's view footprint:
 * where the four corners of the view meet the plane through the camera's
 * target. A corner that looks above the horizon is cut off at
 * {@link FOOTPRINT_REACH} times the camera's distance, so the outline stays
 * on the map. Drawing reuses the 2D minimap's projection (../minimap).
 */
import type { Point } from "../geometry";
import { add, basisOf, distanceOf, scale, type Camera3D } from "./camera3d";
import { intersectPlane, screenRay, type Viewport } from "./projection";

export const FOOTPRINT_REACH = 4;

/** The view's corners on the plane z = camera target's z, as graph x / y. */
export function viewFootprint(camera: Camera3D, viewport: Viewport): Point[] {
  const reach = FOOTPRINT_REACH * Math.max(distanceOf(camera), 1);
  const planeZ = camera.target[2];
  const corners: Point[] = [
    { x: 0, y: 0 },
    { x: viewport.width, y: 0 },
    { x: viewport.width, y: viewport.height },
    { x: 0, y: viewport.height },
  ];
  return corners.map((corner) => {
    const ray = screenRay(camera, corner, viewport);
    const hit = intersectPlane(ray, [0, 0, planeZ], [0, 0, 1]);
    const far = add(ray.origin, scale(ray.direction, reach));
    const point =
      hit &&
      Math.hypot(hit[0] - ray.origin[0], hit[1] - ray.origin[1], hit[2] - ray.origin[2]) <= reach
        ? hit
        : far;
    return { x: point[0], y: point[1] };
  });
}

/** Where the camera is, seen from the top. */
export function cameraOnMap(camera: Camera3D): Point {
  return { x: camera.position[0], y: camera.position[1] };
}

/** The camera moved (with its target) so that it looks at graph point `point` on its target's plane. */
export function lookAtOnMap(camera: Camera3D, point: Point): Camera3D {
  const shift: [number, number, number] = [
    point.x - camera.target[0],
    point.y - camera.target[1],
    0,
  ];
  return {
    ...camera,
    position: add(camera.position, shift),
    target: add(camera.target, shift),
  };
}

/** The viewing direction seen from the top (unit, graph x / y), or null when looking straight down. */
export function headingOnMap(camera: Camera3D): Point | null {
  const { forward } = basisOf(camera);
  const n = Math.hypot(forward[0], forward[1]);
  return n < 1e-6 ? null : { x: forward[0] / n, y: forward[1] / n };
}
