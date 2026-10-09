import {describe, expect, it} from "vitest";

import {outputFormatFromDocument, outputFormatPatch} from "./output-format";

describe("output format", () => {
  it("builds a full-frame landscape patch without the reframe capability", () => {
    const patch = outputFormatPatch("landscape", ["cut", "captions", "center_reframe"]);
    expect(patch.reframe).toBeNull();
    expect(patch.output).toEqual({width: 1920, height: 1080, render_profile: "source"});
    expect(patch.required).toEqual(["cut", "captions"]);
  });

  it("builds the vertical center-crop patch", () => {
    const patch = outputFormatPatch("vertical", ["cut", "captions"]);
    expect(patch.reframe).toEqual({mode: "center", aspect_ratio: "9:16"});
    expect(patch.output).toEqual({width: 1080, height: 1920, render_profile: "vertical_centered"});
    expect(patch.required).toEqual(["cut", "captions", "center_reframe"]);
  });

  it("reads the saved format back from the EDL output", () => {
    expect(outputFormatFromDocument({render_profile: "source"})).toBe("landscape");
    expect(outputFormatFromDocument({render_profile: "vertical_centered"})).toBe("vertical");
    expect(outputFormatFromDocument(undefined)).toBe("vertical");
  });
});
