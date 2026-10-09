"use client";

import Link from "next/link";
import {useEffect, useState} from "react";

import {APIClientError, listProjects, type Project} from "@/lib/api";

import {Button} from "./ui/button";
import {Card} from "./ui/card";

function StatusBadge({status}: {status: string}) {
  return <span className={`status-badge status-${status.replaceAll("_", "-")}`}>{status.replaceAll("_", " ")}</span>;
}

export function ProjectList() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listProjects()
      .then((result) => {
        if (active) setProjects(result.items);
      })
      .catch((cause: unknown) => {
        if (active) {
          setError(cause instanceof APIClientError ? cause.message : "โหลดรายการโปรเจกต์ไม่สำเร็จ");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  if (loading) return <p className="state-message">กำลังโหลดโปรเจกต์…</p>;
  if (error) return <p className="state-message state-error">{error}</p>;
  if (projects.length === 0) {
    return (
      <Card className="empty-state">
        <p className="eyebrow">เริ่มงานแรก</p>
        <h2>ยังไม่มีโปรเจกต์</h2>
        <p>สร้างโปรเจกต์แล้วอัปโหลดวิดีโอเพื่อเริ่ม pipeline</p>
        <Link href="/new"><Button>สร้างโปรเจกต์</Button></Link>
      </Card>
    );
  }

  return (
    <div className="project-grid" aria-label="รายการโปรเจกต์">
      {projects.map((project) => (
        <Link className="project-link" href={`/projects/${project.id}`} key={project.id}>
          <Card className="project-card">
            <div className="project-card-top">
              <span className="project-icon">✦</span>
              <StatusBadge status={project.status} />
            </div>
            <h2>{project.name}</h2>
            <p>{project.instructions || "ยังไม่มีคำอธิบายเพิ่มเติม"}</p>
            <span className="project-card-footer">เปิด workspace →</span>
          </Card>
        </Link>
      ))}
    </div>
  );
}
