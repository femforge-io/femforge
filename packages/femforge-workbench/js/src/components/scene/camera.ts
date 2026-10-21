export type Vector = [number, number, number];
type Quaternion = [number, number, number, number];

/** A camera as VTK holds it, with the parallel scale an orthographic view zooms by. */
export interface Pose {
  position: Vector;
  focalPoint: Vector;
  viewUp: Vector;
  parallelScale: number;
}

const PARALLEL = 0.9995;

function sub(a: Vector, b: Vector): Vector {
  return [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
}

function scale(a: Vector, s: number): Vector {
  return [a[0] * s, a[1] * s, a[2] * s];
}

function dot(a: Vector, b: Vector): number {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function cross(a: Vector, b: Vector): Vector {
  return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function unit(a: Vector): Vector {
  return scale(a, 1 / (Math.hypot(...a) || 1));
}

/** The camera's rotation: the quaternion turning the world axes onto its right, up and backward axes. */
function orientation(pose: Pose): Quaternion {
  const back = unit(sub(pose.position, pose.focalPoint));
  const up = unit(sub(pose.viewUp, scale(back, dot(pose.viewUp, back))));
  const right = cross(up, back);
  const [m00, m10, m20] = right;
  const [m01, m11, m21] = up;
  const [m02, m12, m22] = back;
  const trace = m00 + m11 + m22;

  if (trace > 0) {
    const s = 2 * Math.sqrt(trace + 1);
    return [s / 4, (m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s];
  }
  if (m00 > m11 && m00 > m22) {
    const s = 2 * Math.sqrt(1 + m00 - m11 - m22);
    return [(m21 - m12) / s, s / 4, (m01 + m10) / s, (m02 + m20) / s];
  }
  if (m11 > m22) {
    const s = 2 * Math.sqrt(1 + m11 - m00 - m22);
    return [(m02 - m20) / s, (m01 + m10) / s, s / 4, (m12 + m21) / s];
  }
  const s = 2 * Math.sqrt(1 + m22 - m00 - m11);
  return [(m10 - m01) / s, (m02 + m20) / s, (m12 + m21) / s, s / 4];
}

/** The up and backward axes of a rotation. */
function axes([w, x, y, z]: Quaternion): { up: Vector; back: Vector } {
  return {
    up: [2 * (x * y - w * z), 1 - 2 * (x * x + z * z), 2 * (y * z + w * x)],
    back: [2 * (x * z + w * y), 2 * (y * z - w * x), 1 - 2 * (x * x + y * y)],
  };
}

/** The shortest-arc spherical interpolation of two rotations at `t` in [0, 1]. */
function slerp(a: Quaternion, b: Quaternion, t: number): Quaternion {
  let cos = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3];
  const end: Quaternion = cos < 0 ? [-b[0], -b[1], -b[2], -b[3]] : b;
  cos = Math.abs(cos);
  if (cos > PARALLEL) {
    const mixed = a.map((value, i) => value + (end[i] - value) * t) as Quaternion;
    const length = Math.hypot(...mixed);
    return mixed.map((value) => value / length) as Quaternion;
  }

  const angle = Math.acos(cos);
  const sin = Math.sin(angle);
  const from = Math.sin((1 - t) * angle) / sin;
  const to = Math.sin(t * angle) / sin;

  return a.map((value, i) => value * from + end[i] * to) as Quaternion;
}

/** The camera between two poses at `t` in [0, 1]: orientation by quaternion slerp, focal point linearly, distance and parallel scale geometrically. */
export function between(from: Pose, to: Pose, t: number): Pose {
  if (t <= 0) return from;
  if (t >= 1) return to;

  const { up, back } = axes(slerp(orientation(from), orientation(to), t));
  const focalPoint = from.focalPoint.map((value, i) => value + (to.focalPoint[i] - value) * t) as Vector;
  const near = Math.hypot(...sub(from.position, from.focalPoint));
  const far = Math.hypot(...sub(to.position, to.focalPoint));
  const distance = near ** (1 - t) * far ** t;

  return {
    position: [
      focalPoint[0] + back[0] * distance,
      focalPoint[1] + back[1] * distance,
      focalPoint[2] + back[2] * distance,
    ],
    focalPoint,
    viewUp: up,
    parallelScale: from.parallelScale ** (1 - t) * to.parallelScale ** t,
  };
}
