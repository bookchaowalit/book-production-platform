"use client";

import type {Job} from "@/lib/api";

import {Button} from "./ui/button";
import {Card, CardHeader} from "./ui/card";

export function JobProgress({job, onRetry, retrying}: {job: Job | null; onRetry: () => void; retrying: boolean}) {
  if (!job) {
    return (
      <Card className="job-card">
        <CardHeader><p className="eyebrow">Pipeline</p><h2>ยังไม่ได้เริ่มงาน</h2></CardHeader>
        <p className="muted">อัปโหลดวิดีโอเพื่อให้ระบบเริ่มตรวจ metadata และถอดเสียง</p>
      </Card>
    );
  }

  const failed = job.status === "failed";
  return (
    <Card className="job-card">
      <CardHeader>
        <div>
          <p className="eyebrow">Pipeline job</p>
          <h2>{job.status.replaceAll("_", " ")}</h2>
        </div>
        <span className={`status-badge status-${job.status.replaceAll("_", "-")}`}>{job.progress}%</span>
      </CardHeader>
      <div className="progress-track" aria-label={`ความคืบหน้า ${job.progress}%`}>
        <div className="progress-value" style={{width: `${Math.min(100, Math.max(0, job.progress))}%`}} />
      </div>
      <p className="job-stage">stage: <strong>{job.current_stage || "waiting"}</strong></p>
      {failed && (
        <div className="job-failure">
          <p>{job.error_message || "งานประมวลผลล้มเหลว"}</p>
          <Button disabled={retrying} onClick={onRetry} variant="secondary">
            {retrying ? "กำลัง retry…" : "ลองใหม่"}
          </Button>
        </div>
      )}
    </Card>
  );
}
