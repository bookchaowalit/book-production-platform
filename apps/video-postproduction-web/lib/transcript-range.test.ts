import {describe, expect, it} from "vitest";

import {rangeEndingAt, rangeStartingAt, segmentCoverage} from "./transcript-range";

describe("transcript range selection", () => {
  it("starts at a segment and keeps a later end", () => {
    expect(rangeStartingAt({start: 0, end: 30}, {start: 5.1, end: 9.8})).toEqual({start: 5.1, end: 30});
  });

  it("always keeps the whole chosen segment inside the range", () => {
    expect(rangeStartingAt({start: 0, end: 10}, {start: 9.8, end: 13.1})).toEqual({start: 9.8, end: 13.1});
    expect(rangeEndingAt({start: 14.5, end: 20}, {start: 14.1, end: 16.7})).toEqual({start: 14.1, end: 16.7});
  });

  it("moves the end to the segment end when the old end would precede the new start", () => {
    expect(rangeStartingAt({start: 0, end: 10}, {start: 14.1, end: 16.7})).toEqual({start: 14.1, end: 16.7});
  });

  it("ends at a segment and keeps an earlier start", () => {
    expect(rangeEndingAt({start: 5.1, end: 10}, {start: 24.88, end: 28.08})).toEqual({start: 5.1, end: 28.08});
  });

  it("moves the start to the segment start when the old start would follow the new end", () => {
    expect(rangeEndingAt({start: 40, end: 50}, {start: 9.8, end: 13.1})).toEqual({start: 9.8, end: 13.1});
  });

  it("classifies segments against the selected range", () => {
    const range = {start: 5.1, end: 28};
    expect(segmentCoverage(range, {start: 5.1, end: 9.8})).toBe("inside");
    expect(segmentCoverage(range, {start: 24.88, end: 28.08})).toBe("partial");
    expect(segmentCoverage(range, {start: 30.18, end: 36.54})).toBe("outside");
    expect(segmentCoverage({start: 10, end: 5}, {start: 6, end: 7})).toBe("outside");
  });
});
