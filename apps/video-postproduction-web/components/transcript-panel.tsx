import type {Transcript} from "@/lib/api";
import {segmentCoverage, type TimeSpan} from "@/lib/transcript-range";

import {Button} from "./ui/button";
import {Card, CardHeader} from "./ui/card";

interface TranscriptPanelProps {
  transcript: Transcript | null;
  notReady: boolean;
  loading: boolean;
  range?: TimeSpan;
  onSeek?: (seconds: number) => void;
  onStartAt?: (segment: TimeSpan) => void;
  onEndAt?: (segment: TimeSpan) => void;
  onRetranscribeAccurately?: () => void;
  retranscribingAccurately?: boolean;
}

export function TranscriptPanel({
  transcript,
  notReady,
  loading,
  range,
  onSeek,
  onStartAt,
  onEndAt,
  onRetranscribeAccurately,
  retranscribingAccurately = false,
}: TranscriptPanelProps) {
  if (loading) {
    return <Card><p className="eyebrow">Transcript</p><p className="muted">กำลังโหลด transcript…</p></Card>;
  }
  if (notReady) {
    return (
      <Card className="empty-panel">
        <p className="eyebrow">Transcript</p>
        <h2>ยังถอดเสียงไม่เสร็จ</h2>
        <p className="muted">หน้านี้จะแสดง segment และ word timestamps เมื่อ stage transcribe สำเร็จ</p>
      </Card>
    );
  }
  if (!transcript) return null;
  const segments = transcript.normalized.segments ?? [];
  return (
    <Card className="transcript-card">
      <CardHeader>
        <div>
          <p className="eyebrow">Transcript</p>
          <h2>{transcript.language || "ไม่ระบุภาษา"}</h2>
        </div>
        <span className="muted">Transcription model: {transcript.provider} · {transcript.model}</span>
      </CardHeader>
      {onRetranscribeAccurately && (
        <div className="transcript-actions">
          <p className="muted">ถอดเสียงใหม่ด้วยโมเดล medium เพื่อเน้นความแม่นยำ</p>
          <Button
            disabled={retranscribingAccurately}
            onClick={onRetranscribeAccurately}
            type="button"
            variant="secondary"
          >
            {retranscribingAccurately ? "กำลังถอดเสียงใหม่…" : "ถอดเสียงใหม่แบบแม่นยำ"}
          </Button>
        </div>
      )}
      {segments.length > 0 && onSeek && (
        <p className="muted transcript-hint">กดประโยคเพื่อเล่นวิดีโอตรงนั้น · ใช้ “เริ่มตรงนี้ / จบตรงนี้” เพื่อตั้งช่วง first cut</p>
      )}
      {segments.length === 0 ? (
        <p className="muted">provider ยังไม่ส่ง segment กลับมา</p>
      ) : (
        <div className="transcript-list">
          {segments.map((segment) => {
            const coverage = range ? segmentCoverage(range, segment) : "outside";
            return (
              <article className={`transcript-segment coverage-${coverage}`} key={segment.id}>
                <span className="timecode">{segment.start.toFixed(2)}–{segment.end.toFixed(2)}</span>
                <div>
                  {onSeek ? (
                    <button className="segment-text" type="button" onClick={() => onSeek(segment.start)}>
                      {segment.text}
                    </button>
                  ) : (
                    <p>{segment.text}</p>
                  )}
                  {(onStartAt || onEndAt) && (
                    <div className="segment-actions">
                      {onStartAt && <button type="button" onClick={() => onStartAt(segment)}>เริ่มตรงนี้</button>}
                      {onEndAt && <button type="button" onClick={() => onEndAt(segment)}>จบตรงนี้</button>}
                    </div>
                  )}
                  {segment.words.length > 0 && (
                    // Word tokens are timing detail; for Thai they are sub-word pieces, so keep them collapsed.
                    <details className="word-details">
                      <summary className="muted">word timestamps ({segment.words.length})</summary>
                      <div className="word-row">
                        {segment.words.map((word, index) => <span key={`${segment.id}-${index}`}>{word.text}</span>)}
                      </div>
                    </details>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </Card>
  );
}
