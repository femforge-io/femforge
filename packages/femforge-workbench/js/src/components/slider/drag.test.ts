import { describe, expect, it } from "vitest";

import { holdSelection } from "./drag";

describe("holdSelection", () => {
  it("turns selection off until released, then restores what was there", () => {
    const style = { userSelect: "text", webkitUserSelect: "" };
    const release = holdSelection(style);

    expect(style).toEqual({ userSelect: "none", webkitUserSelect: "none" });

    release();
    expect(style).toEqual({ userSelect: "text", webkitUserSelect: "" });
  });

  it("restores once, so a late second release cannot undo a newer hold", () => {
    const style = { userSelect: "", webkitUserSelect: "" };
    const first = holdSelection(style);
    first();
    const second = holdSelection(style);

    first();
    expect(style.userSelect).toBe("none");

    second();
    expect(style.userSelect).toBe("");
  });
});
