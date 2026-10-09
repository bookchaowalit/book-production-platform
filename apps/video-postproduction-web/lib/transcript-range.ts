export interface TimeSpan {
  start: number;
  end: number;
}

export type SegmentCoverage = "inside" | "partial" | "outside";

const EPSILON = 0.01;

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Start the clip at a segment; keep a later end, but never cut the chosen segment short. */
export function rangeStartingAt(range: TimeSpan, segment: TimeSpan): TimeSpan {
  const start = round(segment.start);
  const end = Number.isFinite(range.end) ? Math.max(range.end, round(segment.end)) : round(segment.end);
  return {start, end};
}

/** End the clip at a segment; keep an earlier start, but never cut the chosen segment short. */
export function rangeEndingAt(range: TimeSpan, segment: TimeSpan): TimeSpan {
  const end = round(segment.end);
  const start = Number.isFinite(range.start) ? Math.min(range.start, round(segment.start)) : round(segment.start);
  return {start, end};
}

export function segmentCoverage(range: TimeSpan, segment: TimeSpan): SegmentCoverage {
  if (!Number.isFinite(range.start) || !Number.isFinite(range.end) || range.end <= range.start) return "outside";
  if (segment.start >= range.start - EPSILON && segment.end <= range.end + EPSILON) return "inside";
  if (segment.end > range.start && segment.start < range.end) return "partial";
  return "outside";
}
