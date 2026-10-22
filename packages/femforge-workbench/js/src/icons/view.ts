import { createLucideIcon } from "lucide-react";

/** Looking at the front: the view axis comes out of the corner of a cube, its near faces outlined. */
export const ViewFront = createLucideIcon("view-front", [
  ["path", { d: "M13 11 3.25 20.75", key: "front-axis" }],
  ["path", { d: "M13 19.5 13 11 4.5 11", key: "front-corner" }],
  ["path", { d: "M11 3.25 20.75 13", key: "front-edge" }],
]);

/** Looking at the back: the front icon turned half a turn. */
export const ViewBack = createLucideIcon("view-back", [
  ["path", { d: "M11 13 20.75 3.25", key: "back-axis" }],
  ["path", { d: "M11 4.5 11 13 19.5 13", key: "back-corner" }],
  ["path", { d: "M13 20.75 3.25 11", key: "back-edge" }],
]);

/** The 2D section view: a plane seen edge-on at a tilt. */
export const View2d = createLucideIcon("view-2d", [["path", { d: "M12 7 21 12 12 17 3 12Z", key: "plane" }]]);
