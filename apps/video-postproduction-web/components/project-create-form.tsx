"use client";

import {useEffect, useState} from "react";
import {useRouter} from "next/navigation";

import {
  APIClientError,
  createProject,
  getTranscriptionCapabilities,
  uploadAsset,
  type TranscriptionQuality,
} from "@/lib/api";

import {Button} from "./ui/button";
import {Card} from "./ui/card";

export function ProjectCreateForm() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [transcriptionQuality, setTranscriptionQuality] = useState<TranscriptionQuality>("fast");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [supportsProjectQuality, setSupportsProjectQuality] = useState<boolean | null>(null);
  const [capabilityLoading, setCapabilityLoading] = useState(true);
  const [capabilityError, setCapabilityError] = useState<string | null>(null);
  const [capabilityAttempt, setCapabilityAttempt] = useState(0);

  useEffect(() => {
    let active = true;
    setCapabilityLoading(true);
    setCapabilityError(null);
    setSupportsProjectQuality(null);
    getTranscriptionCapabilities()
      .then(({supports_project_quality}) => {
        if (active) setSupportsProjectQuality(supports_project_quality);
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setCapabilityError(
          cause instanceof APIClientError
            ? cause.message
            : "ตรวจสอบความสามารถของ provider ไม่สำเร็จ",
        );
      })
      .finally(() => {
        if (active) setCapabilityLoading(false);
      });
    return () => {
      active = false;
    };
  }, [capabilityAttempt]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (supportsProjectQuality === null) return;
    setError(null);
    setBusy(true);
    try {
      const project = await createProject({
        name: name.trim(),
        instructions: instructions.trim() || undefined,
        ...(supportsProjectQuality ? {transcription_quality: transcriptionQuality} : {}),
      });
      if (!file) {
        router.push(`/projects/${project.id}`);
        return;
      }
      const upload = await uploadAsset(project.id, file);
      router.push(`/projects/${project.id}?assetId=${encodeURIComponent(upload.asset_id)}&jobId=${encodeURIComponent(upload.job_id)}`);
    } catch (cause: unknown) {
      setError(cause instanceof APIClientError ? cause.message : "สร้างโปรเจกต์ไม่สำเร็จ");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="form-card">
      <form onSubmit={submit}>
        <label className="field-label" htmlFor="project-name">ชื่อโปรเจกต์</label>
        <input
          id="project-name"
          required
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          placeholder="เช่น Podcast ตอนที่ 01"
          value={name}
        />

        <label className="field-label" htmlFor="project-instructions">Production note <span>optional</span></label>
        <textarea
          id="project-instructions"
          maxLength={10_000}
          onChange={(event) => setInstructions(event.target.value)}
          placeholder="เช่น ตัดช่วงเงียบ, ใช้ซับไทย, output 9:16"
          rows={5}
          value={instructions}
        />

        {capabilityLoading && (
          <p className="muted field-hint" role="status">กำลังตรวจสอบ provider สำหรับการถอดเสียง…</p>
        )}
        {supportsProjectQuality === true && (
          <>
            <label className="field-label" htmlFor="transcription-quality">คุณภาพการถอดเสียง</label>
            <select
              aria-describedby="transcription-quality-wait transcription-quality-benchmark"
              id="transcription-quality"
              onChange={(event) => setTranscriptionQuality(event.target.value as TranscriptionQuality)}
              value={transcriptionQuality}
            >
              <option value="fast">Fast · small</option>
              <option value="accurate">Accurate · medium</option>
            </select>
            <p className="muted field-hint" id="transcription-quality-wait">
              {transcriptionQuality === "fast"
                ? "ใช้เวลาประมาณ ⅓ ของความยาวคลิปบน CPU"
                : "ใช้เวลาประมาณเท่าความยาวคลิปบน CPU"}
            </p>
            <p className="muted field-hint" id="transcription-quality-benchmark">
              Benchmark CPU: คลิปภาษาไทย 75 วินาทีใช้ 26 วินาที (small) และ 71 วินาที (medium)
            </p>
          </>
        )}
        {supportsProjectQuality === false && (
          <p className="muted field-hint" role="status">
            ใช้โมเดลที่ตั้งค่าไว้ใน API runtime; provider นี้ไม่มีการเลือก Fast/Accurate ต่อโปรเจกต์
          </p>
        )}

        <label className="field-label" htmlFor="video-file">วิดีโอต้นฉบับ <span>mp4, mov, webm, mkv</span></label>
        <input
          accept="video/mp4,video/quicktime,video/webm,video/x-matroska"
          id="video-file"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
          type="file"
        />
        {file && <p className="file-hint">พร้อมอัปโหลด: {file.name}</p>}

        {capabilityError && (
          <>
            <p className="form-error" role="alert">{capabilityError}</p>
            <Button
              disabled={capabilityLoading}
              onClick={() => setCapabilityAttempt((attempt) => attempt + 1)}
              type="button"
              variant="secondary"
            >
              ลองตรวจสอบอีกครั้ง
            </Button>
          </>
        )}
        {error && <p className="form-error" role="alert">{error}</p>}
        <Button disabled={busy || !name.trim() || capabilityLoading || supportsProjectQuality === null} type="submit">
          {busy ? "กำลังเตรียมโปรเจกต์…" : file ? "สร้างและเริ่มประมวลผล" : "สร้างโปรเจกต์"}
        </Button>
      </form>
    </Card>
  );
}
