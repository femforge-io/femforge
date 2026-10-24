import { describe, expect, it } from "vitest";

import { diagonal, hexToHsv, hsvToHex, percent, swatchFill } from "./color";

const hex = (digits: string): string => `#${digits}`;

describe("hsv", () => {
  it("round-trips a hex color through hue, saturation and value", () => {
    for (const value of [hex("E0703A"), hex("21918C"), hex("FFFFFF"), hex("000000"), hex("8DA0CB")])
      expect(hsvToHex(hexToHsv(value))).toBe(value);
  });

  it("reads the short form and falls back to black", () => {
    expect(hsvToHex(hexToHsv(hex("f00")))).toBe(hex("FF0000"));
    expect(hexToHsv("red")).toEqual({ h: 0, s: 0, v: 0 });
  });
});

describe("swatchFill", () => {
  const field = "linear-gradient(to right, black 0%, white 100%)";

  it("turns the field colormap corner to corner", () => {
    expect(swatchFill("field", [hex("123456")], field)).toBe("linear-gradient(135deg, black 0%, white 100%)");
    expect(diagonal("none")).toBe("none");
    expect(diagonal("linear-gradient(to left, red 0%, blue 100%)")).toBe("linear-gradient(315deg, red 0%, blue 100%)");
  });

  it("fills one color plainly and splits a group's distinct colors into bands", () => {
    expect(swatchFill("solid", [hex("E0703A")], field)).toBe(hex("E0703A"));
    expect(swatchFill("mixed", [hex("21918C"), hex("E0703A"), hex("21918C")], field)).toBe(
      `linear-gradient(135deg, ${hex("21918C")} 0.0% 50.0%, ${hex("E0703A")} 50.0% 100.0%)`,
    );
  });

  it("lays a mixed group's colors over the field colormap when some of its bodies follow the field", () => {
    expect(swatchFill("mixed", [null, hex("E0703A")], field)).toBe(
      `linear-gradient(135deg, ${hex("E0703A")} 0.0% 50.0%, transparent 50%), linear-gradient(135deg, black 0%, white 100%)`,
    );
  });

  it("falls back to the field colormap when no body has a color of its own", () => {
    expect(swatchFill("mixed", [null, null], field)).toContain("135deg");
    expect(swatchFill("solid", [], undefined)).toBe("transparent");
  });

  it("reads an opacity as a whole percent", () => {
    expect(percent(0.2)).toBe("20 %");
  });
});
