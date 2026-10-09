"use client";

import Link from "next/link";
import {useEffect, useRef, useState} from "react";

import {
  APIClientError,
  enqueueRender,
  getAsset,
  getAssetStreamURL,
  getEDL,
  getJob,
  getLatestJob,
  getRender,
  getRenderDownloadURL,
  getRenderStreamURL,
  getProject,
  getTranscript,
  getTranscriptionCapabilities,
  listAssets,
  retranscribeAccurately,
  retryJob,
  updateEDL,
  type Asset,
  type EDL,
  type Job,
  type Project,
  type RenderOutput,
  type Transcript,
} from "@/lib/api";
import {OUTPUT_FORMATS, outputFormatFromDocument, outputFormatPatch, type OutputFormat} from "@/lib/output-format";
import {rangeEndingAt, rangeStartingAt, type TimeSpan} from "@/lib/transcript-range";

import {JobProgress} from "./job-progress";
import {TranscriptPanel} from "./transcript-panel";
import {Button} from "./ui/button";
import {Card} from "./ui/card";

interface ProjectEditorProps {
  projectId: string;
  assetId?: string;
  jobId?: string;
}

type EditorDocument = Record<string, unknown> & {
  timeline?: Array<Record<string, unknown>>;
  output?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
  source_assets?: Array<Record<string, unknown>>;
};

export function ProjectEditor({projectId, assetId, jobId}: ProjectEditorProps) {
  const [project, setProject] = useState<Project | null>(null);
  const [asset, setAsset] = useState<Asset | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [edl, setEDL] = useState<EDL | null>(null);
  const [start, setStart] = useState("0");
  const [end, setEnd] = useState("10");
  const [captionPreset, setCaptionPreset] = useState("minimal");
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("vertical");
  const [renderOutput, setRenderOutput] = useState<RenderOutput | null>(null);
  const [rendering, setRendering] = useState(false);
  const [transcriptLoading, setTranscriptLoading] = useState(false);
  const [notReady, setNotReady] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [retranscribingAccurately, setRetranscribingAccurately] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const assetIdRef = useRef<string | undefined>(undefined);
  const [qualityCapabilities, setQualityCapabilities] = useState<{
    projectId: string;
    supported: boolean;
  } | null>(null);
  assetIdRef.current = asset?.id;
  const supportsProjectQuality =
    qualityCapabilities?.projectId === projectId && qualityCapabilities.supported;
  const canRetranscribeAccurately =
    supportsProjectQuality &&
    project?.transcription_quality === "fast" &&
    transcript?.provider === "faster_whisper" &&
    transcript.model === "small" &&
    asset !== null &&
    transcript.asset_id === asset.id;

  useEffect(() => {
    let active = true;
    setQualityCapabilities(null);
    getTranscriptionCapabilities()
      .then(({supports_project_quality}) => {
        if (active) {
          setQualityCapabilities({projectId, supported: supports_project_quality});
        }
      })
      .catch((cause: unknown) => {
        if (!active) return;
        setQualityCapabilities({projectId, supported: false});
        setError(
          cause instanceof APIClientError
            ? cause.message
            : "ตรวจสอบความสามารถของ provider ไม่สำเร็จ",
        );
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(() => {
    let active = true;
    setAsset(null);
    setJob(null);
    async function loadProjectContext() {
      try {
        const result = await getProject(projectId);
        if (active) setProject(result);
      } catch (cause: unknown) {
        if (active) setError(cause instanceof APIClientError ? cause.message : "โหลดโปรเจกต์ไม่สำเร็จ");
      }

      try {
        const resolvedAssetId = assetId ?? (await listAssets(projectId)).items[0]?.id;
        if (!resolvedAssetId) return;
        const result = await getAsset(projectId, resolvedAssetId);
        if (active) setAsset(result);
      } catch (cause: unknown) {
        if (active) setError(cause instanceof APIClientError ? cause.message : "โหลด metadata ของวิดีโอไม่สำเร็จ");
      }

      try {
        const result = jobId ? await getJob(jobId) : await getLatestJob(projectId);
        if (active) setJob(result);
      } catch (cause: unknown) {
        if (!active) return;
        if (!jobId && cause instanceof APIClientError && cause.code === "job_not_ready") return;
        setError(cause instanceof APIClientError ? cause.message : "โหลดสถานะงานไม่สำเร็จ");
      }
    }
    void loadProjectContext();
    return () => {
      active = false;
    };
  }, [assetId, jobId, projectId]);

  useEffect(() => {
    if (job && job.status !== "succeeded") return;
    let active = true;
    getEDL(projectId)
      .then((result) => {
        if (!active) return;
        setEDL(result);
        const document = result.document as EditorDocument;
        const first = document.timeline?.[0];
        const source = first?.source as {start?: number; end?: number} | undefined;
        const captions = first?.captions as {preset?: string} | undefined;
        if (source?.start !== undefined) setStart(String(source.start));
        if (source?.end !== undefined) setEnd(String(source.end));
        if (captions?.preset) setCaptionPreset(captions.preset);
        setOutputFormat(outputFormatFromDocument(document.output));
      })
      .catch((cause: unknown) => {
        if (!active || (cause instanceof APIClientError && cause.code === "edl_not_ready")) return;
        setError(cause instanceof APIClientError ? cause.message : "โหลด EDL ไม่สำเร็จ");
      });
    return () => {
      active = false;
    };
  }, [job?.status, projectId]);

  useEffect(() => {
    if (!renderOutput || !["queued", "running", "rendering"].includes(renderOutput.status)) return;
    let active = true;
    const timer = setTimeout(() => {
      getRender(renderOutput.id)
        .then((result) => active && setRenderOutput(result))
        .catch((cause: unknown) => {
          if (active) setError(cause instanceof APIClientError ? cause.message : "โหลดสถานะ render ไม่สำเร็จ");
        });
    }, 2000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [renderOutput]);

  useEffect(() => {
    if (!job || !["queued", "running", "retry_queued"].includes(job.status)) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const result = await getJob(job.id);
        if (!active) return;
        setJob(result);
        if (result.status === "queued" || result.status === "running" || result.status === "retry_queued") {
          timer = setTimeout(poll, 2000);
        }
      } catch (cause: unknown) {
        if (active) setError(cause instanceof APIClientError ? cause.message : "โหลดสถานะงานไม่สำเร็จ");
      }
    };
    void poll();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [job]);

  useEffect(() => {
    // Stages update project/asset status server-side; reload them once the job settles
    // so the header and video metadata do not show stale "created"/"probed" states.
    if (!job || !["succeeded", "failed"].includes(job.status)) return;
    let active = true;
    const currentAssetId = assetIdRef.current;
    getProject(projectId)
      .then((result) => active && setProject(result))
      .catch(() => undefined);
    if (currentAssetId) {
      getAsset(projectId, currentAssetId)
        .then((result) => active && setAsset(result))
        .catch(() => undefined);
    }
    return () => {
      active = false;
    };
  }, [job?.status, projectId]);

  useEffect(() => {
    if (!projectId || (job && job.status !== "succeeded")) return;
    let active = true;
    setTranscriptLoading(true);
    getTranscript(projectId)
      .then((result) => {
        if (active) {
          setTranscript(result);
          setNotReady(false);
        }
      })
      .catch((cause: unknown) => {
        if (!active) return;
        if (cause instanceof APIClientError && cause.code === "transcript_not_ready") setNotReady(true);
        else setError(cause instanceof APIClientError ? cause.message : "โหลด transcript ไม่สำเร็จ");
      })
      .finally(() => active && setTranscriptLoading(false));
    return () => {
      active = false;
    };
  }, [job?.status, projectId]);

  function seekTo(seconds: number) {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seconds;
    void video.play().catch(() => undefined);
  }

  function currentRange(): TimeSpan {
    return {start: Number(start), end: Number(end)};
  }

  function applyRange(range: TimeSpan) {
    setStart(String(range.start));
    setEnd(String(range.end));
  }

  async function retry() {
    if (!job) return;
    setRetrying(true);
    setError(null);
    try {
      setJob(await retryJob(job.id, job.current_stage || "transcribe"));
    } catch (cause: unknown) {
      setError(cause instanceof APIClientError ? cause.message : "retry ไม่สำเร็จ");
    } finally {
      setRetrying(false);
    }
  }

  async function retranscribeAccuratelyAction() {
    if (!asset || !project || !transcript || transcript.asset_id !== asset.id) return;
    setRetranscribingAccurately(true);
    setError(null);
    try {
      const nextJob = await retranscribeAccurately(projectId, asset.id);
      setProject({...project, transcription_quality: "accurate", status: "processing"});
      setJob(nextJob);
    } catch (cause: unknown) {
      setError(cause instanceof APIClientError ? cause.message : "เริ่มถอดเสียงใหม่ไม่สำเร็จ");
    } finally {
      setRetranscribingAccurately(false);
    }
  }

  async function saveAndRender() {
    if (!edl) return;
    const startValue = Number(start);
    const endValue = Number(end);
    const document = edl.document as EditorDocument;
    const first = document.timeline?.[0];
    const sourceAsset = document.source_assets?.[0];
    const sourceDuration = Number(sourceAsset?.duration);
    if (!first || !Number.isFinite(startValue) || !Number.isFinite(endValue) || endValue <= startValue) {
      setError("ช่วงเวลาไม่ถูกต้อง: end ต้องมากกว่า start");
      return;
    }
    if (Number.isFinite(sourceDuration) && endValue > sourceDuration) {
      setError("ช่วงเวลาสิ้นสุดเกินความยาววิดีโอต้นฉบับ");
      return;
    }
    setRendering(true);
    setError(null);
    try {
      const currentVisual = (first.visual as Record<string, unknown> | undefined) ?? {};
      const currentCaptions = (first.captions as Record<string, unknown> | undefined) ?? {};
      const currentCapabilities = (document.capabilities as Record<string, unknown> | undefined) ?? {};
      const required = Array.isArray(currentCapabilities.required) ? currentCapabilities.required.map(String) : [];
      const format = outputFormatPatch(outputFormat, required);
      const nextDocument: EditorDocument = {
        ...document,
        timeline: document.timeline?.map((segment, index) => index === 0 ? {
          ...segment,
          source: {...((segment.source as Record<string, unknown> | undefined) ?? {}), start: startValue, end: endValue},
          visual: {...currentVisual, reframe: format.reframe},
          captions: {...currentCaptions, enabled: true, source: "transcript", format: "ass", preset: captionPreset},
        } : segment),
        output: {...(document.output ?? {}), ...format.output},
        capabilities: {
          ...currentCapabilities,
          required: format.required,
        },
      };
      const updated = await updateEDL(projectId, nextDocument);
      setEDL(updated);
      setRenderOutput(await enqueueRender(projectId, updated.id));
    } catch (cause: unknown) {
      setError(cause instanceof APIClientError ? cause.message : "บันทึกและเริ่ม render ไม่สำเร็จ");
    } finally {
      setRendering(false);
    }
  }

  if (error && !project) return <Card className="state-error"><p>{error}</p></Card>;
  return (
    <div className="editor-layout">
      <div className="editor-main">
        <div className="page-heading compact-heading">
          <div>
            <Link className="back-link" href="/">← Projects</Link>
            <p className="eyebrow">Project editor</p>
            <h1>{project?.name || "กำลังโหลด…"}</h1>
          </div>
          <span className="project-status">{project?.status?.replaceAll("_", " ")}</span>
        </div>
        {asset ? (
          <Card className="video-card">
            <video ref={videoRef} controls preload="metadata" src={getAssetStreamURL(projectId, asset.id)} />
            <div className="video-meta">
              <div><span className="eyebrow">Source</span><strong>{asset.original_filename}</strong></div>
              <div><span className="eyebrow">Status</span><strong>{asset.status}</strong></div>
              <div><span className="eyebrow">Size</span><strong>{(asset.size_bytes / 1024 / 1024).toFixed(2)} MB</strong></div>
            </div>
          </Card>
        ) : (
          <Card className="empty-panel"><h2>ยังไม่มีวิดีโอในโปรเจกต์</h2><p className="muted">กลับไปสร้างโปรเจกต์ใหม่เพื่ออัปโหลด source video</p></Card>
        )}
        <TranscriptPanel
          loading={transcriptLoading}
          notReady={notReady}
          transcript={transcript}
          onRetranscribeAccurately={canRetranscribeAccurately ? retranscribeAccuratelyAction : undefined}
          retranscribingAccurately={retranscribingAccurately}
          range={currentRange()}
          onSeek={seekTo}
          onStartAt={(segment) => applyRange(rangeStartingAt(currentRange(), segment))}
          onEndAt={(segment) => applyRange(rangeEndingAt(currentRange(), segment))}
        />
      </div>
      <aside className="editor-sidebar">
        <JobProgress job={job} onRetry={retry} retrying={retrying} />
        {error && <p className="form-error" role="alert">{error}</p>}
        <Card className="control-card">
          <div className="card-header">
            <div><p className="eyebrow">V1B first cut</p><h2>กำหนดช่วงและ subtitle</h2></div>
            <span className="status-badge">{OUTPUT_FORMATS[outputFormat].badge}</span>
          </div>
          <p className="muted">แก้ไข EDL ที่ persist แล้ว จากนั้นจึงส่งงาน render ด้วย FFmpeg</p>
          <label className="field-label" htmlFor="range-start">Start (วินาที)</label>
          <input id="range-start" type="number" min="0" step="0.01" value={start} onChange={(event) => setStart(event.target.value)} />
          <label className="field-label" htmlFor="range-end">End (วินาที)</label>
          <input id="range-end" type="number" min="0" step="0.01" value={end} onChange={(event) => setEnd(event.target.value)} />
          <label className="field-label" htmlFor="output-format">สัดส่วนวิดีโอ</label>
          <select id="output-format" value={outputFormat} onChange={(event) => setOutputFormat(event.target.value as OutputFormat)}>
            {(Object.keys(OUTPUT_FORMATS) as OutputFormat[]).map((key) => (
              <option key={key} value={key}>{OUTPUT_FORMATS[key].label}</option>
            ))}
          </select>
          <p className="muted field-hint">{OUTPUT_FORMATS[outputFormat].hint}</p>
          <label className="field-label" htmlFor="caption-preset">Caption preset</label>
          <select id="caption-preset" value={captionPreset} onChange={(event) => setCaptionPreset(event.target.value)}>
            <option value="minimal">Minimal</option>
            <option value="bold">Bold</option>
            <option value="karaoke">Karaoke</option>
            <option value="emphasis">Emphasis</option>
          </select>
          <Button disabled={!edl || rendering} onClick={saveAndRender}>
            {rendering ? "กำลังบันทึกและ render…" : "บันทึก EDL และ render"}
          </Button>
          {renderOutput && (
            <div className={`render-result status-${renderOutput.status.replaceAll("_", "-")}`}>
              <span>Render: {renderOutput.status}</span>
              {renderOutput.error && <small>{renderOutput.error}</small>}
              {renderOutput.status === "succeeded" && (
                <span className="render-links">
                  <a href={getRenderStreamURL(renderOutput.id)} target="_blank" rel="noreferrer">Preview</a>
                  <a href={getRenderDownloadURL(renderOutput.id)}>Download</a>
                </span>
              )}
            </div>
          )}
        </Card>
        <Card className="contract-card">
          <p className="eyebrow">Capability boundary</p>
          <p>V1B รองรับ source range, 9:16 center crop หรือ 16:9 เต็มเฟรม และ caption preset เท่านั้น; active speaker, color และ B-roll ยังไม่เปิดใช้งาน</p>
        </Card>
      </aside>
    </div>
  );
}
