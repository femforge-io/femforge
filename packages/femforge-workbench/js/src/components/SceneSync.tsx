import { useEffect, useRef } from "react";

export interface SceneSyncProps {
  /** The trame ref of the LocalView whose camera drives the triad and whose canvas is measured. */
  viewRef: string;
  /** The wasm id of the main renderer's camera. */
  cameraId: number;
  /** The wasm id of the vtkMatrix4x4 the triad actor carries as its user matrix. */
  matrixId: number;
  /** Called with `{width, height, ratio}` of the mirror's canvas in device pixels, whenever it changes. */
  onResize?: (size: CanvasSize) => void;
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
}

interface NativeSession {
  observe(id: number, event: string, callback: () => void): unknown;
  unObserve(id: number, tag: unknown): void;
  invoke(id: number, method: string, args: unknown[]): unknown;
}

interface RemoteSession {
  cameraIds: Set<number>;
  renderWindowIdsWithRunningEventLoops: Set<number>;
  native: NativeSession;
  getState(id: number): unknown;
}

interface LocalViewHandle {
  getRemoteSession(): RemoteSession | undefined;
}

const RETRY_MS = 100;
const SETTLE_MS = 150;
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

/** Turns the mirror's triad with its camera and tells the server how large the mirror's canvas is; renders nothing but an anchor to find that canvas by. */
export function SceneSync({ viewRef, cameraId, matrixId, onResize }: SceneSyncProps) {
  const anchor = useRef<HTMLSpanElement>(null);

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
