import { animate } from "motion";
import { useReducedMotion } from "motion/react";
import { useEffect, useRef } from "react";

import { between, type Pose } from "@/components/scene/camera";
import { easeInstrument, token } from "@/lib/motion";

export interface SceneSyncProps {
  /** The trame ref of the LocalView whose camera drives the triad and whose canvas is measured. */
  viewRef: string;
  /** The wasm id of the main renderer's camera. */
  cameraId: number;
  /** The wasm id of the vtkMatrix4x4 the triad actor carries as its user matrix. */
  matrixId: number;
  /** The wasm id of the main renderer, whose clipping range follows a glide. */
  rendererId?: number;
  /** A camera the mirror glides to; each new `seq` starts a glide from wherever the camera is. */
  move?: CameraMove | null;
  /** The wasm id of the hidden outline actor of each entity id. */
  highlights?: Record<string, number> | null;
  /** The entity id whose outline shows; empty shows none. */
  highlighted?: string | null;
  /** Called with `{width, height, ratio}` of the mirror's canvas in device pixels, whenever it changes. */
  onResize?: (size: CanvasSize) => void;
}

export interface CameraMove {
  seq: number;
  position: Vector;
  focal_point: Vector;
  view_up: Vector;
  parallel_scale: number;
}

export interface CanvasSize {
  width: number;
  height: number;
  ratio: number;
}

type Vector = [number, number, number];

interface CameraState {
  Position: Vector;
  FocalPoint: Vector;
  ViewUp: Vector;
  ParallelScale?: number;
}

interface NativeSession {
  observe(id: number, event: string, callback: () => void): unknown;
  unObserve(id: number, tag: unknown): void;
  invoke(id: number, method: string, args: unknown[]): unknown;
  getAllDependencies(id: number): ArrayLike<number>;
}

interface RemoteSession {
  cameraIds: Set<number>;
  renderWindowIdsWithRunningEventLoops: Set<number>;
  native: NativeSession;
  getState(id: number): unknown;
}

interface LocalViewHandle {
  getRemoteSession(): RemoteSession | undefined;
  render(): void;
}

const RETRY_MS = 100;
const SETTLE_MS = 150;
const OUTLINE_CHECK_MS = 200;
const MODIFIED = "ModifiedEvent";

function normalized([x, y, z]: Vector): Vector {
  const length = Math.hypot(x, y, z) || 1;

  return [x / length, y / length, z / length];
}

/** The rotation that shows the world axes as the camera sees them: rows right, up and back of the view basis, as a 4x4. */
function viewRotation({ Position, FocalPoint, ViewUp }: CameraState): number[] {
  const direction = normalized([FocalPoint[0] - Position[0], FocalPoint[1] - Position[1], FocalPoint[2] - Position[2]]);
  const along = ViewUp[0] * direction[0] + ViewUp[1] * direction[1] + ViewUp[2] * direction[2];
  const up = normalized([
    ViewUp[0] - along * direction[0],
    ViewUp[1] - along * direction[1],
    ViewUp[2] - along * direction[2],
  ]);
  const right: Vector = [
    direction[1] * up[2] - direction[2] * up[1],
    direction[2] * up[0] - direction[0] * up[2],
    direction[0] * up[1] - direction[1] * up[0],
  ];

  return [...right, 0, ...up, 0, -direction[0], -direction[1], -direction[2], 0, 0, 0, 0, 1];
}

function isCameraState(value: unknown): value is CameraState {
  if (typeof value !== "object" || value === null) return false;

  const state = value as Record<string, unknown>;

  return Array.isArray(state.Position) && Array.isArray(state.FocalPoint) && Array.isArray(state.ViewUp);
}

/** The object the session knows under an id, undefined while the browser has not instantiated it (the session lists the server's camera ids first and then parses an empty state). */
function stateOf(session: RemoteSession, id: number): unknown {
  try {
    return session.getState(id);
  } catch {
    return undefined;
  }
}

/** The ids of every object the mirror has built for its render windows; unlike getState, it never logs an error for an id the mirror has not built, such as a redrawn scene's outline before its sync lands. */
function builtIds(session: RemoteSession): Set<number> {
  const ids = new Set<number>();
  for (const window of session.renderWindowIdsWithRunningEventLoops ?? []) {
    for (const id of Array.from(session.native.getAllDependencies(window))) ids.add(id);
  }

  return ids;
}

/** Turns the mirror's triad with its camera, glides the camera to each move the server sends, and tells the server how large the mirror's canvas is; renders nothing but an anchor to find that canvas by. */
export function SceneSync({
  viewRef,
  cameraId,
  matrixId,
  rendererId,
  move,
  highlights,
  highlighted,
  onResize,
}: SceneSyncProps) {
  const anchor = useRef<HTMLSpanElement>(null);
  const reduced = useReducedMotion();
  const outlined = useRef<number | undefined>(undefined);

  // Showing an outline is a visibility flip in the browser, so it follows the pointer without a server round trip.
  useEffect(() => {
    const next = highlighted ? highlights?.[highlighted] : undefined;

    const show = (): void => {
      const handle = window.trame?.refs[viewRef] as LocalViewHandle | undefined;
      const session = handle?.getRemoteSession?.();
      if (!handle || !session) return;

      const built = builtIds(session);
      const known = (id: number | undefined): id is number => id !== undefined && built.has(id);
      const previous = outlined.current;
      let changed = false;
      // The wasm bindings take VTK's int flag; a JS boolean leaves the actor as it was.
      if (previous !== next && known(previous)) {
        session.native.invoke(previous, "SetVisibility", [0]);
        changed = true;
      }

      outlined.current = next;
      if (known(next) && session.native.invoke(next, "GetVisibility", []) === 0) {
        session.native.invoke(next, "SetVisibility", [1]);
        changed = true;
      }

      if (changed) handle.render();
    };

    show();
    if (next === undefined) return undefined;

    // A redrawn scene's actors reach the mirror after their ids, and every sync writes the server's hidden state back.
    const timer = window.setInterval(show, OUTLINE_CHECK_MS);

    return () => window.clearInterval(timer);
  }, [viewRef, highlights, highlighted]);

  useEffect(() => {
    if (!move) return undefined;

    const handle = window.trame?.refs[viewRef] as LocalViewHandle | undefined;
    const session = handle?.getRemoteSession?.();
    const start = session ? stateOf(session, cameraId) : undefined;
    if (!handle || !session || !isCameraState(start)) return undefined;

    const from: Pose = {
      position: start.Position,
      focalPoint: start.FocalPoint,
      viewUp: start.ViewUp,
      parallelScale: start.ParallelScale ?? move.parallel_scale,
    };
    const to: Pose = {
      position: move.position,
      focalPoint: move.focal_point,
      viewUp: move.view_up,
      parallelScale: move.parallel_scale,
    };
    const show = (pose: Pose): void => {
      session.native.invoke(cameraId, "SetPosition", pose.position);
      session.native.invoke(cameraId, "SetFocalPoint", pose.focalPoint);
      session.native.invoke(cameraId, "SetViewUp", pose.viewUp);
      session.native.invoke(cameraId, "SetParallelScale", [pose.parallelScale]);
      if (rendererId !== undefined) session.native.invoke(rendererId, "ResetCameraClippingRange", []);
      handle.render();
    };
    const duration = reduced ? 0 : token("--duration-camera") / 1000;
    if (duration === 0) {
      show(to);

      return undefined;
    }

    const glide = animate(0, 1, { duration, ease: easeInstrument(), onUpdate: (t) => show(between(from, to, t)) });
    // Any input in the view takes the camera over from the glide.
    const canvas = anchor.current?.parentElement?.querySelector("canvas");
    const stop = (): void => glide.stop();
    canvas?.addEventListener("pointerdown", stop);
    canvas?.addEventListener("wheel", stop, { passive: true });

    return () => {
      glide.stop();
      canvas?.removeEventListener("pointerdown", stop);
      canvas?.removeEventListener("wheel", stop);
    };
    // A move is identified by its seq; a re-render with the same move must not restart it.
  }, [move?.seq, viewRef, cameraId, rendererId, reduced]);

  useEffect(() => {
    let timer = 0;
    let session: RemoteSession | undefined;
    let tag: unknown;

    const sync = (): void => {
      if (!session) return;

      const state = stateOf(session, cameraId);
      if (!isCameraState(state)) return;

      session.native.invoke(matrixId, "DeepCopy", [viewRotation(state)]);
    };

    // getState on an object the browser has not built yet logs a vtkObjectManager error, and the view starts its event loop only after the first sync built them.
    const ready = (found: RemoteSession | undefined): found is RemoteSession =>
      !!found?.renderWindowIdsWithRunningEventLoops?.size &&
      !!found.cameraIds?.has(cameraId) &&
      isCameraState(stateOf(found, cameraId)) &&
      stateOf(found, matrixId) !== undefined;

    // The LocalView mounts its wasm session asynchronously and both objects appear well after the session lists them.
    const attach = (): void => {
      const handle = window.trame?.refs[viewRef] as LocalViewHandle | undefined;
      const found = handle?.getRemoteSession?.();
      if (!ready(found)) {
        timer = window.setTimeout(attach, RETRY_MS);

        return;
      }

      session = found;
      tag = session.native.observe(cameraId, MODIFIED, sync);
      sync();
    };

    attach();

    return () => {
      window.clearTimeout(timer);
      if (session && tag !== undefined) session.native.unObserve(cameraId, tag);
    };
  }, [viewRef, cameraId, matrixId]);

  useEffect(() => {
    if (!onResize) return undefined;

    let timer = 0;
    let observer: ResizeObserver | undefined;
    let last = "";

    // The box, never canvas.width: the LocalView sets the backing store afterwards and that assignment is not itself a resize.
    const measure = (entry: ResizeObserverEntry): CanvasSize => {
      const ratio = window.devicePixelRatio || 1;
      const box = entry.devicePixelContentBoxSize?.[0];

      return box
        ? { width: box.inlineSize, height: box.blockSize, ratio }
        : {
            width: Math.round(entry.contentRect.width * ratio),
            height: Math.round(entry.contentRect.height * ratio),
            ratio,
          };
    };

    const report = (entry: ResizeObserverEntry): void => {
      const size = measure(entry);
      const seen = `${size.width}x${size.height}`;
      if (!size.width || !size.height || seen === last) return;

      last = seen;
      window.clearTimeout(timer);
      timer = window.setTimeout(() => onResize(size), SETTLE_MS);
    };

    const observe = (): void => {
      const canvas = anchor.current?.parentElement?.querySelector("canvas");
      if (!canvas) {
        timer = window.setTimeout(observe, RETRY_MS);

        return;
      }

      observer = new ResizeObserver((entries) => report(entries[entries.length - 1]));
      try {
        observer.observe(canvas, { box: "device-pixel-content-box" });
      } catch {
        observer.observe(canvas);
      }
    };

    observe();

    return () => {
      window.clearTimeout(timer);
      observer?.disconnect();
    };
  }, [viewRef, onResize]);

  return <span ref={anchor} hidden />;
}
