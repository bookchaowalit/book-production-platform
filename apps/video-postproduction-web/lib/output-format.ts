export type OutputFormat = "vertical" | "landscape";

export const OUTPUT_FORMATS: Record<OutputFormat, {label: string; badge: string; hint: string}> = {
  vertical: {label: "9:16 แนวตั้ง (Reels / TikTok / Shorts)", badge: "9:16 center", hint: "ครอปกลางภาพให้เป็นแนวตั้ง"},
  landscape: {label: "16:9 แนวนอน (YouTube)", badge: "16:9 full frame", hint: "เก็บภาพเต็มเฟรม ถ้าสัดส่วนต้นฉบับไม่ตรงจะเติมขอบดำ"},
};

interface FormatPatch {
  reframe: {mode: "center"; aspect_ratio: "9:16"} | null;
  output: {width: number; height: number; render_profile: "vertical_centered" | "source"};
  required: string[];
}

/** EDL fields that change with the output format; everything else in the document is preserved. */
export function outputFormatPatch(format: OutputFormat, currentRequired: string[]): FormatPatch {
  const base = currentRequired.filter((capability) => capability !== "center_reframe");
  if (format === "landscape") {
    return {
      reframe: null,
      output: {width: 1920, height: 1080, render_profile: "source"},
      required: Array.from(new Set([...base, "cut", "captions"])),
    };
  }
  return {
    reframe: {mode: "center", aspect_ratio: "9:16"},
    output: {width: 1080, height: 1920, render_profile: "vertical_centered"},
    required: Array.from(new Set([...base, "cut", "captions", "center_reframe"])),
  };
}

export function outputFormatFromDocument(output: Record<string, unknown> | undefined): OutputFormat {
  return output?.render_profile === "source" ? "landscape" : "vertical";
}
