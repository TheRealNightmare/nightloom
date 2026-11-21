/**
 * The sign-in page's 3D loom, drawn with three.js. Loaded on demand by
 * ./LoomBackdrop once the sign-in form is interactive and the browser is idle;
 * it shares the three-vendor chunk with the 3D graph view.
 *
 * Flat token colors on --bg, output as they are (linear, no tone mapping); no
 * glow, fog, particles or camera movement. The cloth's oldest rows fade into
 * the background by blending their colors toward --bg.
 *
 * Cost: at most 30 frames a second on a clock of its own (./frameLoop), a
 * device pixel ratio of at most 1.5, nothing drawn while the tab is hidden or
 * the canvas is off-screen, everything released by `dispose`.
 */
import {
  BufferAttribute,
  BufferGeometry,
  Color,
  DynamicDrawUsage,
  InstancedBufferAttribute,
  InstancedMesh,
  LinearSRGBColorSpace,
  LineBasicMaterial,
  LineSegments,
  NoToneMapping,
  PerspectiveCamera,
  PlaneGeometry,
  Scene,
  ShaderMaterial,
  Vector3,
  WebGLRenderer,
} from "three";

import { createFrameLoop, type FrameLoop } from "./frameLoop";
import { CAMERA, LOOM, START_SECONDS, loomFrame, type LoomFrame } from "./geometry";

export type Rgb = readonly [number, number, number];

export interface LoomColors {
  background: Rgb;
  /** The warp threads (--hair). */
  warp: Rgb;
  /** The weft thread (--brass). */
  weft: Rgb;
  /** Knot colors, in the order of KNOT_TYPES. */
  knots: readonly Rgb[];
}

export interface LoomSceneOptions {
  /** Called once the first frame is on the canvas. */
  onReady?: () => void;
  /** Called when the scene stops for good (the WebGL context was lost). */
  onError?: (error: Error) => void;
}

export interface LoomScene {
  setColors: (colors: LoomColors) => void;
  dispose: () => void;
  /** For tests and diagnostics. */
  loop: FrameLoop;
}

export const MAX_FPS = 30;
export const MAX_PIXEL_RATIO = 1.5;

const KNOT_VERTEX = /* glsl */ `
attribute vec3 aColor;
attribute vec2 aStyle;
varying vec2 vOffset;
varying vec3 vColor;
varying float vFade;

void main() {
  vec4 center = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
  center.xy += position.xy * aStyle.x;
  gl_Position = projectionMatrix * center;
  vOffset = position.xy;
  vColor = aColor;
  vFade = aStyle.y;
}
`;

// A knot as in the graph: a ring in the type color, a face of the background
// and a dot in the type color.
const KNOT_FRAGMENT = /* glsl */ `
uniform vec3 uBackground;
varying vec2 vOffset;
varying vec3 vColor;
varying float vFade;

void main() {
  float r = length(vOffset);
  float aa = max(fwidth(r), 1e-4);
  if (r > 1.0 + aa) discard;
  vec3 color = (r > 0.62 || r < 0.26) ? vColor : uBackground;
  float edge = 1.0 - smoothstep(1.0 - aa, 1.0 + aa, r);
  gl_FragColor = vec4(mix(uBackground, color, vFade * edge), 1.0);
}
`;

const STEPS = (LOOM.warps - 1) * LOOM.samplesPerGap;
/** Every row's polyline as segments (two vertices each), the newest one's extra point included. */
const WEFT_VERTICES = LOOM.rows * (STEPS + 1) * 2;
const KNOT_CAPACITY = LOOM.rows * LOOM.warps;

function rawColor(rgb: Rgb): Color {
  return new Color().setRGB(rgb[0], rgb[1], rgb[2], LinearSRGBColorSpace);
}

function mix(from: Rgb, to: Rgb, amount: number): [number, number, number] {
  return [
    from[0] + (to[0] - from[0]) * amount,
    from[1] + (to[1] - from[1]) * amount,
    from[2] + (to[2] - from[2]) * amount,
  ];
}

/** Fills the line and knot buffers from one frame of the loom. */
export function writeFrame(
  frame: LoomFrame,
  colors: LoomColors,
  out: {
    warpPositions: Float32Array;
    warpColors: Float32Array;
    weftPositions: Float32Array;
    weftColors: Float32Array;
    knotMatrices: Float32Array;
    knotColors: Float32Array;
    knotStyles: Float32Array;
  },
): { warpVertices: number; weftVertices: number; knots: number } {
  let w = 0;
  for (const [near, far] of frame.warps) {
    // The far end fades into the background, like the oldest rows.
    for (const [point, color] of [
      [near, colors.warp],
      [far, mix(colors.background, colors.warp, 0.15)],
    ] as const) {
      out.warpPositions.set(point, w * 3);
      out.warpColors.set(color, w * 3);
      w++;
    }
  }

  let v = 0;
  for (const row of frame.rows) {
    const color = mix(colors.background, colors.weft, row.fade);
    for (let i = 1; i < row.points.length && v + 2 <= WEFT_VERTICES; i++) {
      const a = row.points[i - 1];
      const b = row.points[i];
      if (!a || !b) continue;
      out.weftPositions.set(a, v * 3);
      out.weftColors.set(color, v * 3);
      out.weftPositions.set(b, (v + 1) * 3);
      out.weftColors.set(color, (v + 1) * 3);
      v += 2;
    }
  }

  let k = 0;
  for (const knot of frame.knots) {
    if (k >= KNOT_CAPACITY) break;
    const m = k * 16;
    out.knotMatrices.fill(0, m, m + 16);
    out.knotMatrices[m] = 1;
    out.knotMatrices[m + 5] = 1;
    out.knotMatrices[m + 10] = 1;
    out.knotMatrices[m + 15] = 1;
    out.knotMatrices[m + 12] = knot.position[0];
    // Just above the weft, so the thread passes under the knot.
    out.knotMatrices[m + 13] = knot.position[1] + 0.01;
    out.knotMatrices[m + 14] = knot.position[2];
    out.knotColors.set(colors.knots[knot.type] ?? colors.weft, k * 3);
    out.knotStyles[k * 2] = LOOM.knotRadius * knot.scale;
    out.knotStyles[k * 2 + 1] = knot.fade;
    k++;
  }
  return { warpVertices: w, weftVertices: v, knots: k };
}

function lineSegments(positions: Float32Array, colors: Float32Array): LineSegments {
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(positions, 3).setUsage(DynamicDrawUsage));
  geometry.setAttribute("color", new BufferAttribute(colors, 3).setUsage(DynamicDrawUsage));
  const lines = new LineSegments(geometry, new LineBasicMaterial({ vertexColors: true }));
  lines.frustumCulled = false;
  return lines;
}

/**
 * Starts the loom on `canvas` (sized by its parent). Throws when WebGL is not
 * available; the caller shows the static drawing instead.
 */
export function startLoomScene(
  canvas: HTMLCanvasElement,
  initialColors: LoomColors,
  { onReady, onError }: LoomSceneOptions = {},
): LoomScene {
  const renderer = new WebGLRenderer({
    canvas,
    antialias: true,
    alpha: false,
    powerPreference: "low-power",
  });
  renderer.outputColorSpace = LinearSRGBColorSpace;
  renderer.toneMapping = NoToneMapping;
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, MAX_PIXEL_RATIO));

  let colors = initialColors;
  const scene = new Scene();
  const camera = new PerspectiveCamera(CAMERA.fovDegrees, 1, 0.1, 100);
  camera.position.set(...CAMERA.position);
  camera.lookAt(new Vector3(...CAMERA.target));

  const out = {
    warpPositions: new Float32Array(LOOM.warps * 2 * 3),
    warpColors: new Float32Array(LOOM.warps * 2 * 3),
    weftPositions: new Float32Array(WEFT_VERTICES * 3),
    weftColors: new Float32Array(WEFT_VERTICES * 3),
    knotMatrices: new Float32Array(KNOT_CAPACITY * 16),
    knotColors: new Float32Array(KNOT_CAPACITY * 3),
    knotStyles: new Float32Array(KNOT_CAPACITY * 2),
  };
  const warp = lineSegments(out.warpPositions, out.warpColors);
  const weft = lineSegments(out.weftPositions, out.weftColors);
  // Warp over weft where they cross at the same depth: drawn after it.
  weft.renderOrder = 1;
  warp.renderOrder = 2;

  const knotGeometry = new PlaneGeometry(2, 2);
  knotGeometry.setAttribute("aColor", new InstancedBufferAttribute(out.knotColors, 3));
  knotGeometry.setAttribute("aStyle", new InstancedBufferAttribute(out.knotStyles, 2));
  const knotMaterial = new ShaderMaterial({
    vertexShader: KNOT_VERTEX,
    fragmentShader: KNOT_FRAGMENT,
    uniforms: { uBackground: { value: new Vector3(...colors.background) } },
  });
  const knots = new InstancedMesh(knotGeometry, knotMaterial, KNOT_CAPACITY);
  knots.instanceMatrix = new InstancedBufferAttribute(out.knotMatrices, 16);
  knots.instanceMatrix.setUsage(DynamicDrawUsage);
  knots.frustumCulled = false;
  knots.renderOrder = 3;
  scene.add(warp, weft, knots);

  let ready = false;
  let lastSeconds = START_SECONDS;
  const draw = (seconds: number) => {
    lastSeconds = seconds;
    const counts = writeFrame(loomFrame(seconds), colors, out);
    for (const lines of [warp, weft]) {
      lines.geometry.getAttribute("position").needsUpdate = true;
      lines.geometry.getAttribute("color").needsUpdate = true;
    }
    warp.geometry.setDrawRange(0, counts.warpVertices);
    weft.geometry.setDrawRange(0, counts.weftVertices);
    knots.count = counts.knots;
    knots.instanceMatrix.needsUpdate = true;
    knotGeometry.getAttribute("aColor").needsUpdate = true;
    knotGeometry.getAttribute("aStyle").needsUpdate = true;
    renderer.render(scene, camera);
    if (!ready) {
      ready = true;
      onReady?.();
    }
  };

  const applyColors = () => {
    renderer.setClearColor(rawColor(colors.background), 1);
    (knotMaterial.uniforms["uBackground"]?.value as Vector3).set(...colors.background);
  };
  applyColors();

  const resize = () => {
    const parent = canvas.parentElement;
    const width = parent?.clientWidth ?? canvas.clientWidth;
    const height = parent?.clientHeight ?? canvas.clientHeight;
    if (width <= 0 || height <= 0) return;
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  };
  resize();

  const loop = createFrameLoop({ draw, startSeconds: START_SECONDS, fps: MAX_FPS });

  const resizeObserver = new ResizeObserver(() => {
    resize();
    if (loop.running()) draw(lastSeconds);
  });
  if (canvas.parentElement) resizeObserver.observe(canvas.parentElement);

  const onVisibility = () => {
    if (document.hidden) loop.pause("hidden");
    else loop.resume("hidden");
  };
  document.addEventListener("visibilitychange", onVisibility);
  onVisibility();

  const intersection =
    typeof IntersectionObserver === "function"
      ? new IntersectionObserver((entries) => {
          const visible = entries.some((entry) => entry.isIntersecting);
          if (visible) loop.resume("offscreen");
          else loop.pause("offscreen");
        })
      : null;
  intersection?.observe(canvas);

  let disposed = false;
  const dispose = () => {
    if (disposed) return;
    disposed = true;
    loop.stop();
    resizeObserver.disconnect();
    intersection?.disconnect();
    document.removeEventListener("visibilitychange", onVisibility);
    canvas.removeEventListener("webglcontextlost", onContextLost);
    scene.remove(warp, weft, knots);
    for (const item of [warp, weft]) {
      item.geometry.dispose();
      (item.material as LineBasicMaterial).dispose();
    }
    knotGeometry.dispose();
    knotMaterial.dispose();
    knots.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  };

  function onContextLost(event: Event) {
    event.preventDefault();
    loop.stop();
    onError?.(new Error("The WebGL context was lost."));
  }
  canvas.addEventListener("webglcontextlost", onContextLost);

  return {
    setColors(next) {
      colors = next;
      applyColors();
      // Repaint now, even while paused (the next visible frame is right).
      if (!disposed) draw(lastSeconds);
    },
    dispose,
    loop,
  };
}
