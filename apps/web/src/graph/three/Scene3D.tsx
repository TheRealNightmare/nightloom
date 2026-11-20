/**
 * The three.js part of the 3D view, inside a React Three Fiber canvas:
 * threads (one LineSegments), knots (one InstancedMesh per shape: entities
 * and collapsed groups, camera-facing discs drawn by a small shader in the
 * 2D knot's rings) and the faint warp guide. Everything it draws comes from
 * the typed arrays of ./scene, filled by GraphCanvas3D; this component only
 * uploads them when their version changes.
 *
 * The canvas renders on demand (frameloop "demand"): nothing runs while
 * nothing changes. There is no glow, bloom, fog or gradient: flat token
 * colors, output as they are (linear, no tone mapping), on --bg.
 */
import {
  createRoot,
  extend,
  useFrame,
  useThree,
  type ReconcilerRoot,
  type RootState,
} from "@react-three/fiber";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
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
  PlaneGeometry,
  ShaderMaterial,
  Vector3,
  type PerspectiveCamera,
} from "three";

import {
  FACE_FRACTION,
  GROUP_RING_GAP,
  GROUP_RING_WIDTH,
  HALO_DASH_PX,
  OUTER_GAP,
  RING_WIDTH,
} from "../encoding";
import { TAN_HALF_FOV } from "./camera3d";
import { MAX_ATTENUATION, MIN_ATTENUATION } from "./picking";
import type { KnotBuffers, Rgb, ThreadBuffers } from "./scene";

/** What GraphCanvas3D fills and the scene uploads (see ./scene). */
export interface SceneBuffers {
  entities: KnotBuffers;
  groups: KnotBuffers;
  threads: ThreadBuffers;
  /** The warp guide's line vertices (x, y, z) and how many are in use. */
  warp: Float32Array;
  warpVertices: number;
  /** Changes when arrays are replaced (more room was needed). */
  allocation: number;
  /** Changes when colors, sizes or counts change. */
  styleVersion: number;
  /** Changes when positions change. */
  positionVersion: number;
}

export interface SceneColors {
  background: Rgb;
  surface: Rgb;
  selection: Rgb;
  grid: Rgb;
}

export interface SceneApi {
  invalidate: () => void;
  canvas: HTMLCanvasElement;
}

/** The camera and the buffers, created and owned by GraphCanvas3D (mutable, outside React state). */
export interface SceneStage {
  camera: PerspectiveCamera;
  buffers: SceneBuffers;
}

export interface Scene3DProps {
  stage: { readonly current: SceneStage | null };
  colors: SceneColors;
  /** The camera's distance to its target: knots there are drawn at their 2D size. */
  referenceDepth: () => number;
  onCreated: (api: SceneApi) => void;
  /** Before each frame is drawn: move what moves, place labels (viewport in CSS pixels). */
  onFrame: (viewport: { width: number; height: number }) => void;
}

const KNOT_VERTEX = /* glsl */ `
attribute vec3 aColor;
attribute vec4 aStyle;
uniform float uPxToView;
uniform float uRefDepth;
uniform float uOuterGap;
varying vec2 vOffset;
varying vec3 vColor;
varying vec4 vStyle;
varying float vTotal;

void main() {
  vec4 center = modelViewMatrix * instanceMatrix * vec4(0.0, 0.0, 0.0, 1.0);
  float depth = max(-center.z, 1e-6);
  float attenuation = clamp(uRefDepth / depth, ${MIN_ATTENUATION.toFixed(3)}, ${MAX_ATTENUATION.toFixed(3)});
  float total = aStyle.x
    + (aStyle.z > 0.0 ? aStyle.z + uOuterGap : 0.0)
    + (aStyle.y > 0.0 ? aStyle.y + uOuterGap : 0.0);
  center.xy += position.xy * total * attenuation * depth * uPxToView;
  gl_Position = projectionMatrix * center;
  vOffset = position.xy;
  vColor = aColor;
  vStyle = aStyle;
  vTotal = total;
}
`;

const KNOT_FRAGMENT = /* glsl */ `
uniform vec3 uBackground;
uniform vec3 uSurface;
uniform vec3 uSelection;
uniform float uOuterGap;
uniform float uRing;
uniform float uGroup;
uniform float uGroupGap;
uniform float uFace;
varying vec2 vOffset;
varying vec3 vColor;
varying vec4 vStyle;
varying float vTotal;

void main() {
  // Distance from the center in the knot's own pixels (before perspective).
  float r = length(vOffset) * vTotal;
  float aa = max(fwidth(r), 1e-4);
  if (r > vTotal + aa * 0.5) discard;
  float size = vStyle.x;
  float halo = vStyle.y;
  float pin = vStyle.z;
  vec3 color;
  float pinOuter = size + (pin > 0.0 ? uOuterGap + pin : 0.0);
  if (r > size) {
    if (pin > 0.0 && r > size + uOuterGap && r <= pinOuter) {
      color = uSelection;
    } else if (halo > 0.0 && r > pinOuter + uOuterGap) {
      // The selection halo is dashed, as in 2D.
      float turns = atan(vOffset.y, vOffset.x) / 6.2831853 + 0.5;
      float dashes = max(6.0, floor(6.2831853 * vTotal / ${HALO_DASH_PX.toFixed(1)}));
      if (fract(turns * dashes) < 0.4) discard;
      color = uSelection;
    } else {
      discard;
    }
  } else {
    float inner = size - uRing;
    if (r > inner) {
      color = vColor;
    } else if (uGroup > 0.5) {
      // A group: a second ring inside the first, an empty face (its count is a label).
      color = (r > inner - uGroupGap - uRing && r <= inner - uGroupGap) ? vColor : uSurface;
    } else {
      // The face (surface color), then the center dot in the type color.
      color = r > inner * (1.0 - uFace) ? uSurface : vColor;
    }
  }
  gl_FragColor = vec4(mix(uBackground, color, vStyle.w), 1.0);
}
`;

function vec(rgb: Rgb): Vector3 {
  return new Vector3(rgb[0], rgb[1], rgb[2]);
}

function rawColor(rgb: Rgb): Color {
  // The tokens' sRGB values, output unconverted (the canvas is linear).
  return new Color().setRGB(rgb[0], rgb[1], rgb[2], LinearSRGBColorSpace);
}

function knotMaterial(group: boolean, colors: SceneColors): ShaderMaterial {
  return new ShaderMaterial({
    vertexShader: KNOT_VERTEX,
    fragmentShader: KNOT_FRAGMENT,
    uniforms: {
      uPxToView: { value: 0.001 },
      uRefDepth: { value: 1 },
      uOuterGap: { value: OUTER_GAP },
      uRing: { value: group ? GROUP_RING_WIDTH : RING_WIDTH },
      uGroup: { value: group ? 1 : 0 },
      uGroupGap: { value: GROUP_RING_GAP },
      uFace: { value: FACE_FRACTION },
      uBackground: { value: vec(colors.background) },
      uSurface: { value: vec(colors.surface) },
      uSelection: { value: vec(colors.selection) },
    },
  });
}

/** An InstancedMesh over `buffers`' arrays (shared, not copied). */
function knotMesh(buffers: KnotBuffers, material: ShaderMaterial): InstancedMesh {
  const geometry = new PlaneGeometry(2, 2);
  const mesh = new InstancedMesh(geometry, material, Math.max(buffers.capacity, 1));
  mesh.instanceMatrix = new InstancedBufferAttribute(buffers.matrix, 16);
  mesh.instanceMatrix.setUsage(DynamicDrawUsage);
  geometry.setAttribute("aColor", new InstancedBufferAttribute(buffers.color, 3));
  geometry.setAttribute("aStyle", new InstancedBufferAttribute(buffers.style, 4));
  mesh.count = buffers.count;
  // Bounds change every frame something moves; the whole graph is always drawn.
  mesh.frustumCulled = false;
  // Picking happens in screen space (./picking), never by raycasting.
  mesh.raycast = () => undefined;
  mesh.renderOrder = 2;
  return mesh;
}

function lines(positions: Float32Array, colors: Float32Array | null, material: LineBasicMaterial) {
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new BufferAttribute(positions, 3).setUsage(DynamicDrawUsage));
  if (colors) geometry.setAttribute("color", new BufferAttribute(colors, 3));
  const segments = new LineSegments(geometry, material);
  segments.frustumCulled = false;
  segments.raycast = () => undefined;
  return segments;
}

interface Objects {
  entities: InstancedMesh;
  groups: InstancedMesh;
  threads: LineSegments;
  warp: LineSegments;
  materials: {
    entities: ShaderMaterial;
    groups: ShaderMaterial;
    threads: LineBasicMaterial;
    warp: LineBasicMaterial;
  };
}

function createObjects(buffers: SceneBuffers, colors: SceneColors): Objects {
  const materials = {
    entities: knotMaterial(false, colors),
    groups: knotMaterial(true, colors),
    threads: new LineBasicMaterial({ vertexColors: true }),
    warp: new LineBasicMaterial({ color: rawColor(colors.grid) }),
  };
  const threads = lines(buffers.threads.positions, buffers.threads.colors, materials.threads);
  threads.renderOrder = 1;
  const warp = lines(buffers.warp, null, materials.warp);
  return {
    entities: knotMesh(buffers.entities, materials.entities),
    groups: knotMesh(buffers.groups, materials.groups),
    threads,
    warp,
    materials,
  };
}

function disposeObjects(objects: Objects): void {
  for (const item of [objects.entities, objects.groups, objects.threads, objects.warp]) {
    item.geometry.dispose();
  }
  objects.entities.dispose();
  objects.groups.dispose();
  for (const material of Object.values(objects.materials)) material.dispose();
}

function setColors(objects: Objects, colors: SceneColors): void {
  for (const material of [objects.materials.entities, objects.materials.groups]) {
    (material.uniforms["uBackground"]?.value as Vector3).copy(vec(colors.background));
    (material.uniforms["uSurface"]?.value as Vector3).copy(vec(colors.surface));
    (material.uniforms["uSelection"]?.value as Vector3).copy(vec(colors.selection));
  }
  objects.materials.warp.color = rawColor(colors.grid);
}

/** Uploads what changed in `buffers` since `seen`, and sets the per-frame uniforms. */
function upload(
  objects: Objects,
  buffers: SceneBuffers,
  seen: { style: number; position: number },
  height: number,
  referenceDepth: number,
): void {
  const { entities, groups, threads, warp } = objects;
  if (seen.style !== buffers.styleVersion) {
    seen.style = buffers.styleVersion;
    for (const [mesh, data] of [
      [entities, buffers.entities],
      [groups, buffers.groups],
    ] as const) {
      mesh.count = data.count;
      (mesh.geometry.getAttribute("aColor") as InstancedBufferAttribute).needsUpdate = true;
      (mesh.geometry.getAttribute("aStyle") as InstancedBufferAttribute).needsUpdate = true;
    }
    (threads.geometry.getAttribute("color") as BufferAttribute).needsUpdate = true;
    threads.geometry.setDrawRange(0, buffers.threads.vertices);
  }
  if (seen.position !== buffers.positionVersion) {
    seen.position = buffers.positionVersion;
    entities.instanceMatrix.needsUpdate = true;
    groups.instanceMatrix.needsUpdate = true;
    (threads.geometry.getAttribute("position") as BufferAttribute).needsUpdate = true;
    (warp.geometry.getAttribute("position") as BufferAttribute).needsUpdate = true;
    warp.geometry.setDrawRange(0, buffers.warpVertices);
  }
  const pxToView = (2 * TAN_HALF_FOV) / Math.max(height, 1);
  for (const material of [objects.materials.entities, objects.materials.groups]) {
    const px = material.uniforms["uPxToView"];
    const ref = material.uniforms["uRefDepth"];
    if (px) px.value = pxToView;
    if (ref) ref.value = referenceDepth;
  }
}

function SceneContent({ stage, colors, referenceDepth, onCreated, onFrame }: Scene3DProps) {
  const gl = useThree((state) => state.gl);
  const scene = useThree((state) => state.scene);
  const set = useThree((state) => state.set);
  const invalidate = useThree((state) => state.invalidate);
  // The objects in the scene; replaced when the buffers get new arrays.
  const objectsRef = useRef<{ objects: Objects; allocation: number } | null>(null);
  const seen = useRef({ style: -1, position: -1 });
  const colorsRef = useRef(colors);

  // GraphCanvas3D's camera is the scene's camera (R3F keeps its aspect).
  useEffect(() => {
    const current = stage.current;
    if (current) set({ camera: current.camera });
  }, [stage, set]);

  useEffect(
    () => () => {
      const held = objectsRef.current;
      objectsRef.current = null;
      if (!held) return;
      scene.remove(
        held.objects.warp,
        held.objects.threads,
        held.objects.entities,
        held.objects.groups,
      );
      disposeObjects(held.objects);
    },
    [scene],
  );

  // Theme: repaint in place.
  useEffect(() => {
    colorsRef.current = colors;
    const held = objectsRef.current;
    if (held) setColors(held.objects, colors);
    invalidate();
  }, [colors, invalidate]);

  const created = useRef(onCreated);
  useEffect(() => {
    created.current = onCreated;
  });
  useEffect(() => {
    created.current({ invalidate, canvas: gl.domElement });
  }, [gl, invalidate]);

  useFrame((state: RootState) => {
    const current = stage.current;
    if (!current) return;
    onFrame({ width: state.size.width, height: state.size.height });
    const { buffers } = current;
    let held = objectsRef.current;
    if (held?.allocation !== buffers.allocation) {
      if (held) {
        scene.remove(
          held.objects.warp,
          held.objects.threads,
          held.objects.entities,
          held.objects.groups,
        );
        disposeObjects(held.objects);
      }
      const objects = createObjects(buffers, colorsRef.current);
      scene.add(objects.warp, objects.threads, objects.entities, objects.groups);
      held = { objects, allocation: buffers.allocation };
      objectsRef.current = held;
      seen.current = { style: -1, position: -1 };
    }
    upload(held.objects, buffers, seen.current, state.size.height, referenceDepth());
  });

  // The background: --bg (the components are the token's sRGB values, output unconverted).
  return <color attach="background" args={colors.background} />;
}

/** R3F's pointer events are off: picking is done in screen space (./picking). */
const NO_EVENTS = () => ({ enabled: false, priority: 0 });

// The only three.js element written as JSX. R3F's <Canvas> would register the
// whole THREE namespace (and so bundle all of three.js); a root of our own
// registers just this, and three.js is tree-shaken to what the view uses.
extend({ Color });

interface Size {
  width: number;
  height: number;
}

/**
 * The canvas: an R3F root of our own (createRoot), sized to its parent, with
 * rendering on demand, linear output and no tone mapping. A renderer that
 * cannot start (no WebGL context) throws to the nearest error boundary.
 */
export function Scene3D(props: Scene3DProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const rootRef = useRef<ReconcilerRoot<HTMLCanvasElement> | null>(null);
  const [size, setSize] = useState<Size | null>(null);
  const [failure, setFailure] = useState<Error | null>(null);
  if (failure) throw failure;

  useLayoutEffect(() => {
    const parent = canvasRef.current?.parentElement;
    if (!parent) return;
    const measure = () => {
      const box = parent.getBoundingClientRect();
      setSize((current) =>
        current?.width === box.width && current.height === box.height
          ? current
          : { width: box.width, height: box.height },
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    return () => {
      observer.disconnect();
    };
  }, []);

  useLayoutEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !size || size.width <= 0 || size.height <= 0) return;
    rootRef.current ??= createRoot(canvas);
    const root = rootRef.current;
    root
      .configure({
        size: { ...size, top: 0, left: 0 },
        frameloop: "demand",
        linear: true,
        flat: true,
        dpr: [1, 2],
        events: NO_EVENTS,
        gl: { antialias: true, powerPreference: "high-performance" },
      })
      .then(
        () => {
          root.render(<SceneContent {...props} />);
        },
        (error: unknown) => {
          setFailure(error instanceof Error ? error : new Error(String(error)));
        },
      );
  });

  useEffect(
    () => () => {
      rootRef.current?.unmount();
      rootRef.current = null;
    },
    [],
  );

  return <canvas ref={canvasRef} className="graph3d-canvas" aria-hidden="true" />;
}

export default Scene3D;
