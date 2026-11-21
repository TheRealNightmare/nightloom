/**
 * The sign-in loom as a still drawing: the same warp, weft and knots as the
 * 3D scene (src/auth/loom/loomScene.ts), projected through the same camera
 * from src/auth/loom/geometry.ts. Shown until the scene is ready, and instead
 * of it with reduced motion, without WebGL, with Save-Data, or when the scene
 * fails to load. Plain SVG with token colors (no inline styles or scripts, so
 * the Content-Security-Policy allows it); purely decorative.
 */
import { typeToken } from "../domain/entities";
import {
  CAMERA,
  KNOT_TYPES,
  LOOM,
  START_SECONDS,
  loomFrame,
  projectPoint,
  type Vec3,
} from "../auth/loom/geometry";

const WIDTH = 600;
const HEIGHT = 480;
const PROJECTION = { aspect: WIDTH / HEIGHT };
const TAN_HALF_FOV = Math.tan((CAMERA.fovDegrees * Math.PI) / 360);

function toView(point: Vec3): { x: number; y: number; depth: number } {
  const p = projectPoint(point, PROJECTION);
  return { x: ((p.x + 1) / 2) * WIDTH, y: ((1 - p.y) / 2) * HEIGHT, depth: p.depth };
}

const round = (value: number) => Math.round(value * 10) / 10;

function polyline(points: readonly Vec3[]): string {
  return points
    .map((point) => {
      const { x, y } = toView(point);
      return `${String(round(x))},${String(round(y))}`;
    })
    .join(" ");
}

/** One still frame of the loom, computed once. */
const FRAME = loomFrame(START_SECONDS);

const WARPS = FRAME.warps.map(([near, far]) => ({ near: toView(near), far: toView(far) }));

const ROWS = FRAME.rows.map((row) => ({
  key: row.index,
  points: polyline(row.points),
  opacity: round(row.fade),
}));

const KNOTS = FRAME.knots
  .filter((knot) => knot.scale > 0)
  .map((knot) => {
    const { x, y, depth } = toView(knot.position);
    const radius = ((LOOM.knotRadius * knot.scale) / depth / TAN_HALF_FOV) * (HEIGHT / 2);
    const type = KNOT_TYPES[knot.type] ?? "domain";
    return {
      key: `${String(round(x))}-${String(round(y))}`,
      x: round(x),
      y: round(y),
      radius: round(radius),
      color: `var(${typeToken(type)})`,
      opacity: round(knot.fade),
    };
  });

export function LoomArt({ className = "loom-art" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
      fill="none"
      aria-hidden="true"
      focusable="false"
      preserveAspectRatio="xMidYMid slice"
      data-testid="loom-static"
    >
      {ROWS.map((row) => (
        <polyline
          key={`weft-${String(row.key)}`}
          points={row.points}
          stroke="var(--brass)"
          strokeOpacity={row.opacity}
          strokeWidth="1.25"
          strokeLinejoin="round"
        />
      ))}
      {WARPS.map(({ near, far }) => (
        <line
          key={`warp-${String(round(near.x))}`}
          x1={round(near.x)}
          y1={round(near.y)}
          x2={round(far.x)}
          y2={round(far.y)}
          stroke="var(--hair)"
          strokeWidth="1"
        />
      ))}
      {KNOTS.map((knot) => (
        <g key={knot.key} opacity={knot.opacity}>
          <circle
            cx={knot.x}
            cy={knot.y}
            r={knot.radius * 0.81}
            fill="var(--bg)"
            stroke={knot.color}
            strokeWidth={knot.radius * 0.38}
          />
          <circle cx={knot.x} cy={knot.y} r={knot.radius * 0.26} fill={knot.color} />
        </g>
      ))}
    </svg>
  );
}
