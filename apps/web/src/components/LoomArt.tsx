/**
 * The sign-in page's decoration: a few warp and weft threads in perspective,
 * knotted where they cross. A static stand-in for the 3D loom planned for a
 * later release; purely decorative (hidden from assistive technology).
 */

/** Warp threads run from a vanishing point above the art toward the viewer. */
const WARP = [-3, -2, -1, 0, 1, 2, 3].map((i) => ({
  top: 240 + i * 34,
  bottom: 240 + i * 92,
}));
/** Weft rows, closer together toward the horizon. */
const WEFT = [120, 168, 226, 296, 380];

function xAt(warp: { top: number; bottom: number }, y: number): number {
  const t = (y - 60) / (440 - 60);
  return warp.top + (warp.bottom - warp.top) * t;
}

/** A weft row sags toward the viewer: deeper for nearer rows. */
const sagOf = (row: number) => 6 + row * 3;

/** The weft row `row` as a quadratic curve between the outermost warps. */
function weftCurve(row: number) {
  const y = WEFT[row] ?? 0;
  const first = WARP[0] ?? { top: 0, bottom: 0 };
  const last = WARP[WARP.length - 1] ?? { top: 0, bottom: 0 };
  return { y, x1: xAt(first, y), x2: xAt(last, y), sag: sagOf(row) };
}

/** The point of weft `row` above `x` (the curve is linear in x, so s is exact). */
function weftY(row: number, x: number): number {
  const { y, x1, x2, sag } = weftCurve(row);
  const s = (x - x1) / (x2 - x1);
  return y + 2 * s * (1 - s) * sag;
}

/** Where the threads are knotted (warp index, weft index). */
const KNOTS: readonly (readonly [number, number])[] = [
  [2, 1],
  [4, 2],
  [3, 3],
  [5, 3],
  [1, 4],
  [4, 4],
];

export function LoomArt() {
  return (
    <svg
      className="loom-art"
      viewBox="0 0 480 440"
      fill="none"
      aria-hidden="true"
      focusable="false"
      preserveAspectRatio="xMidYMid meet"
    >
      {WARP.map((warp) => (
        <line
          key={`warp-${String(warp.top)}`}
          x1={warp.top}
          y1={60}
          x2={warp.bottom}
          y2={440}
          stroke="var(--hair)"
          strokeWidth="1"
        />
      ))}
      {WEFT.map((y, row) => {
        const { x1, x2, sag } = weftCurve(row);
        return (
          <path
            key={`weft-${String(y)}`}
            d={`M${String(x1)} ${String(y)} Q${String((x1 + x2) / 2)} ${String(y + sag)} ${String(x2)} ${String(y)}`}
            stroke={row === 2 ? "var(--brass)" : "var(--hair)"}
            strokeWidth={row === 2 ? 1.5 : 1}
          />
        );
      })}
      {KNOTS.map(([w, row]) => {
        const warp = WARP[w];
        const y = WEFT[row];
        if (!warp || y === undefined) return null;
        const r = 3 + row * 1.2;
        const x = xAt(warp, y);
        return (
          <circle
            key={`knot-${String(w)}-${String(row)}`}
            cx={x}
            cy={weftY(row, x)}
            r={r}
            fill="var(--bg)"
            stroke="var(--brass)"
            strokeWidth="1.25"
          />
        );
      })}
    </svg>
  );
}
