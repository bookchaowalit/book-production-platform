'use client';

import { useCallback, useEffect, useState } from 'react';

import {
  api,
  formatBytes,
  HubError,
  isPreviewable,
  NEXT_REVIEW,
  QA_LABELS,
  REVIEW_LABELS,
  shortId,
  type ArtifactRecord,
  type ArtifactRow,
  type LineageNode,
  type ProjectArtifact,
  type ProjectSummary,
  type Registry,
  type SendOption,
} from '@/lib/api';

type Tab = 'library' | 'projects';

const message = (cause: unknown) => (cause instanceof HubError ? cause.message : 'เกิดข้อผิดพลาดที่ไม่คาดคิด');

function Notice({ kind, text }: { kind: 'error' | 'success'; text: string | null }) {
  if (!text) return null;
  return <p className={`notice ${kind}`} role={kind === 'error' ? 'alert' : 'status'}>{text}</p>;
}

function Lineage({ node, onOpen }: { node: LineageNode; onOpen: (id: string) => void }) {
  return (
    <li>
      <button type="button" onClick={() => onOpen(node.artifact_id)}>{node.filename}</button>{' '}
      <span className="muted">{node.kind} · {node.producer.tool_id}</span>
      {node.inputs && node.inputs.length > 0 && (
        <ul className="tree">{node.inputs.map((child) => <Lineage key={child.artifact_id} node={child} onOpen={onOpen} />)}</ul>
      )}
      {node.inputs_truncated ? <span className="muted"> (+{node.inputs_truncated})</span> : null}
    </li>
  );
}

function SendForm({ artifact, option, onDone }: { artifact: ArtifactRecord; option: SendOption; onDone: (id: string) => void }) {
  const [params, setParams] = useState<Record<string, string>>({});
  const [extra, setExtra] = useState('');
  const [candidates, setCandidates] = useState<ArtifactRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);

  useEffect(() => {
    if (!option.available || option.extra_kinds.length === 0) return;
    let active = true;
    Promise.all(option.extra_kinds.map((kind) => api.artifacts({ kind })))
      .then((lists) => active && setCandidates(lists.flatMap((list) => list.items)))
      .catch(() => active && setCandidates([]));
    return () => {
      active = false;
    };
  }, [option]);

  if (!option.available) {
    return (
      <div className="send unavailable">
        <h3>ส่งต่อไป {option.to_tool}</h3>
        <p>ยังไม่เปิดใช้เส้นทางนี้ ({option.status === 'planned' ? 'อยู่ในแผน' : 'ยังไม่มีตัวเชื่อม'})</p>
      </div>
    );
  }
  const needsExtra = option.extra_kinds.length > 0;
  const ready = option.required_params.every((key) => params[key]?.trim()) && (!needsExtra || extra !== '');
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setResult(null);
    try {
      const response = await api.send({
        artifact_id: artifact.artifact_id, to: option.to_tool, params, with: extra ? [extra] : undefined,
      });
      setResult(`สร้างไฟล์ใหม่ ${response.outputs.length} ไฟล์ (${response.handoff_id})`);
      if (response.outputs[0]) onDone(response.outputs[0].artifact_id);
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <form className="send" onSubmit={submit}>
      <h3>ส่งต่อไป {option.to_tool}</h3>
      {needsExtra && (
        <label>
          ไฟล์ประกอบ ({option.extra_kinds.join(', ')})
          <select value={extra} onChange={(event) => setExtra(event.target.value)}>
            <option value="">— เลือกไฟล์ —</option>
            {candidates.map((item) => (
              <option key={item.artifact_id} value={item.artifact_id}>
                {item.filename} · {item.tool_id} · {shortId(item.artifact_id)}
              </option>
            ))}
          </select>
        </label>
      )}
      {option.required_params.map((key) => (
        <label key={key}>
          {key}
          <input value={params[key] ?? ''} onChange={(event) => setParams({ ...params, [key]: event.target.value })} />
        </label>
      ))}
      <div className="actions">
        <button type="submit" disabled={busy || !ready}>{busy ? 'กำลังส่งและรันเครื่องมือ…' : 'ส่งต่อ'}</button>
      </div>
      <Notice kind="error" text={error} />
      <Notice kind="success" text={result} />
    </form>
  );
}

function ArtifactDetail({ id, onOpen }: { id: string; onOpen: (id: string) => void }) {
  const [record, setRecord] = useState<ArtifactRecord | null>(null);
  const [lineage, setLineage] = useState<LineageNode | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setRecord(null);
    setLineage(null);
    setError(null);
    Promise.all([api.artifact(id), api.lineage(id)])
      .then(([nextRecord, nextLineage]) => {
        if (!active) return;
        setRecord(nextRecord);
        setLineage(nextLineage);
      })
      .catch((cause) => active && setError(message(cause)));
    return () => {
      active = false;
    };
  }, [id]);

  if (error) return <div className="panel"><Notice kind="error" text={error} /></div>;
  if (!record) return <div className="panel"><p className="muted" role="status">กำลังโหลดรายละเอียด…</p></div>;
  return (
    <div className="panel" aria-live="polite">
      <h2>{record.filename}</h2>
      <dl className="facts">
        <dt>ชนิด</dt><dd>{record.kind} <span className="muted">({record.media_type})</span></dd>
        <dt>เครื่องมือ</dt><dd>{record.producer.tool_id} {record.producer.tool_version}</dd>
        <dt>งาน</dt><dd><code>{record.producer.job_id}</code></dd>
        <dt>QA</dt><dd><span className={`pill ${record.qa.status}`}>{QA_LABELS[record.qa.status]}</span></dd>
        <dt>โปรเจกต์</dt><dd>{record.project_id ?? '—'}</dd>
        <dt>ขนาด</dt><dd>{formatBytes(record.byte_size)}</dd>
        <dt>ความลับ</dt><dd>{record.privacy_class}</dd>
        <dt>รหัสไฟล์</dt><dd><code>{record.artifact_id}</code></dd>
      </dl>
      {isPreviewable(record.media_type) && record.media_type.startsWith('image/') && (
        // eslint-disable-next-line @next/next/no-img-element
        <img className="preview" src={api.contentUrl(record.artifact_id)} alt={`ตัวอย่าง ${record.filename}`} />
      )}
      <div className="actions">
        <a href={api.contentUrl(record.artifact_id)} download={record.filename}>ดาวน์โหลดไฟล์</a>
      </div>
      <h3 style={{ marginTop: 14 }}>ที่มาของไฟล์</h3>
      {lineage && (lineage.inputs?.length ?? 0) > 0
        ? <ul className="tree">{lineage.inputs!.map((node) => <Lineage key={node.artifact_id} node={node} onOpen={onOpen} />)}</ul>
        : <p className="muted">ไม่มีไฟล์ต้นทางที่ลงทะเบียนไว้</p>}
      {record.used_by.length > 0 && (
        <>
          <h3 style={{ marginTop: 14 }}>ถูกใช้ต่อใน</h3>
          <ul className="tree">
            {record.used_by.map((usedId) => (
              <li key={usedId}><button type="button" onClick={() => onOpen(usedId)}>{shortId(usedId)}</button></li>
            ))}
          </ul>
        </>
      )}
      {record.sends.length > 0
        ? record.sends.map((option) => <SendForm key={option.handoff_id} artifact={record} option={option} onDone={onOpen} />)
        : <p className="muted" style={{ marginTop: 14 }}>ไฟล์ชนิดนี้ยังไม่มีเส้นทางส่งต่อ</p>}
    </div>
  );
}

function Library({ registry }: { registry: Registry | null }) {
  const [filters, setFilters] = useState({ kind: '', tool: '', project: '' });
  const [items, setItems] = useState<ArtifactRow[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    api.artifacts(filters).then((data) => setItems(data.items)).catch((cause) => {
      setItems([]);
      setError(message(cause));
    });
  }, [filters]);
  useEffect(load, [load]);

  return (
    <div className="layout">
      <section className="panel" aria-labelledby="library-heading">
        <h2 id="library-heading">คลังไฟล์</h2>
        <div className="filters">
          <label>ชนิดไฟล์
            <select value={filters.kind} onChange={(event) => setFilters({ ...filters, kind: event.target.value })}>
              <option value="">ทั้งหมด</option>
              {Object.keys(registry?.kinds ?? {}).sort().map((kind) => <option key={kind} value={kind}>{kind}</option>)}
            </select>
          </label>
          <label>เครื่องมือ
            <select value={filters.tool} onChange={(event) => setFilters({ ...filters, tool: event.target.value })}>
              <option value="">ทั้งหมด</option>
              {Object.keys(registry?.tools ?? {}).sort().map((tool) => <option key={tool} value={tool}>{tool}</option>)}
            </select>
          </label>
          <label>โปรเจกต์
            <input value={filters.project} placeholder="เช่น pod-1"
              onChange={(event) => setFilters({ ...filters, project: event.target.value.trim() })} />
          </label>
        </div>
        <Notice kind="error" text={error} />
        {items === null && <p className="muted" role="status">กำลังโหลดคลังไฟล์…</p>}
        {items?.length === 0 && !error && <p className="empty">ยังไม่มีไฟล์ตามเงื่อนไขนี้ — ใช้ <code>python -m production_hub ingest</code> เพื่อนำผลงานเข้าคลัง</p>}
        <ul className="list">
          {items?.map((item) => (
            <li key={item.artifact_id}>
              <button type="button" className="row" aria-current={selected === item.artifact_id}
                onClick={() => setSelected(item.artifact_id)}>
                <span className="name">{item.filename}</span>
                <span className={`pill ${item.qa_status}`}>{QA_LABELS[item.qa_status]}</span>
                <span className="meta">
                  <span>{item.kind}</span><span>{item.tool_id}</span><span>{formatBytes(item.byte_size)}</span>
                  {item.project_id && <span>โปรเจกต์ {item.project_id}</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
      <aside>
        {selected ? <ArtifactDetail id={selected} onOpen={setSelected} /> : (
          <div className="panel"><p className="muted">เลือกไฟล์เพื่อดูที่มา ดาวน์โหลด หรือส่งต่อไปเครื่องมืออื่น</p></div>
        )}
      </aside>
    </div>
  );
}

function ProjectDetail({ projectId }: { projectId: string }) {
  const [data, setData] = useState<{ project: ProjectSummary; artifacts: ProjectArtifact[] } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api.project(projectId).then(setData).catch((cause) => setError(message(cause)));
  }, [projectId]);
  useEffect(() => {
    setData(null);
    setError(null);
    setNotice(null);
    load();
  }, [load]);

  async function run(action: () => Promise<string>) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      setNotice(await action());
      load();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!data) return <div className="panel">{error ? <Notice kind="error" text={error} /> : <p className="muted" role="status">กำลังโหลดโปรเจกต์…</p>}</div>;
  const approved = data.artifacts.filter((item) => item.review_status === 'approved').length;
  return (
    <div className="panel">
      <h2>{data.project.name}</h2>
      <p className="muted">{data.project.client ?? 'ไม่ระบุลูกค้า'} · {data.artifacts.length} ไฟล์ · อนุมัติแล้ว {approved}</p>
      <Notice kind="error" text={error} />
      <Notice kind="success" text={notice} />
      <div className="actions" style={{ margin: '10px 0' }}>
        <button type="button" disabled={busy || approved === 0}
          onClick={() => run(async () => {
            const result = await api.packageProject(projectId);
            return `สร้างแพ็กเกจส่งมอบ ${result.files} ไฟล์แล้วที่ ${result.path} — ส่งให้ลูกค้าด้วยตัวเองหลังตรวจอีกครั้ง`;
          })}>
          สร้างแพ็กเกจส่งมอบ
        </button>
      </div>
      {data.artifacts.length === 0 && <p className="empty">ยังไม่มีไฟล์ในโปรเจกต์นี้</p>}
      <ul className="list">
        {data.artifacts.map((item) => (
          <li key={item.artifact_id} className="row" style={{ cursor: 'default' }}>
            <span className="name">{item.filename}</span>
            <span className={`pill ${item.review_status}`}>{REVIEW_LABELS[item.review_status]}</span>
            <span className="meta">
              <span>{item.kind}</span><span>{item.tool_id}</span>
              <span className={`pill ${item.qa_status}`}>{QA_LABELS[item.qa_status]}</span>
            </span>
            <span className="actions" style={{ gridColumn: '1 / -1' }}>
              {NEXT_REVIEW[item.review_status].map((next) => (
                <button key={next} type="button" className="secondary" disabled={busy}
                  onClick={() => run(async () => {
                    await api.review(projectId, { artifact_id: item.artifact_id, status: next });
                    return `${item.filename}: ${REVIEW_LABELS[item.review_status]} → ${REVIEW_LABELS[next]}`;
                  })}>
                  {REVIEW_LABELS[next]}
                </button>
              ))}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Projects() {
  const [items, setItems] = useState<ProjectSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [form, setForm] = useState({ project_id: '', name: '', client: '' });
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api.projects().then((data) => setItems(data.items)).catch((cause) => {
      setItems([]);
      setError(message(cause));
    });
  }, []);
  useEffect(load, [load]);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      await api.createProject({ project_id: form.project_id, name: form.name || form.project_id, client: form.client || undefined });
      setSelected(form.project_id);
      setForm({ project_id: '', name: '', client: '' });
      load();
    } catch (cause) {
      setError(message(cause));
    }
  }

  return (
    <div className="layout">
      <section>
        <div className="panel">
          <h2>โปรเจกต์</h2>
          {items === null && <p className="muted" role="status">กำลังโหลด…</p>}
          {items?.length === 0 && <p className="empty">ยังไม่มีโปรเจกต์</p>}
          <ul className="list">
            {items?.map((item) => (
              <li key={item.project_id}>
                <button type="button" className="row" aria-current={selected === item.project_id}
                  onClick={() => setSelected(item.project_id)}>
                  <span className="name">{item.name}</span>
                  <span className="pill">{item.approved ?? 0}/{item.artifacts} อนุมัติ</span>
                  <span className="meta"><code>{item.project_id}</code>{item.client && <span>{item.client}</span>}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
        <form className="panel form" onSubmit={create}>
          <h2>สร้างโปรเจกต์</h2>
          <label>รหัสโปรเจกต์ (a-z, 0-9, -)
            <input required pattern="[a-z0-9]+(-[a-z0-9]+)*" value={form.project_id}
              onChange={(event) => setForm({ ...form, project_id: event.target.value })} />
          </label>
          <label>ชื่อโปรเจกต์<input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
          <label>ลูกค้า<input value={form.client} onChange={(event) => setForm({ ...form, client: event.target.value })} /></label>
          <Notice kind="error" text={error} />
          <div className="actions"><button type="submit" disabled={!form.project_id}>สร้าง</button></div>
        </form>
      </section>
      <aside>
        {selected ? <ProjectDetail projectId={selected} /> : (
          <div className="panel"><p className="muted">เลือกโปรเจกต์เพื่อตรวจงานและสร้างแพ็กเกจส่งมอบ</p></div>
        )}
      </aside>
    </div>
  );
}

export default function ConsoleClient() {
  const [tab, setTab] = useState<Tab>('library');
  const [health, setHealth] = useState<'checking' | 'ok' | 'down'>('checking');
  const [registry, setRegistry] = useState<Registry | null>(null);

  useEffect(() => {
    api.health().then(() => setHealth('ok')).catch(() => setHealth('down'));
    api.registry().then(setRegistry).catch(() => setRegistry(null));
  }, []);

  return (
    <main className="shell">
      <header className="masthead">
        <div>
          <h1>Production Console</h1>
          <p>คลังไฟล์งานผลิต · ที่มาของไฟล์ · ส่งต่อข้ามเครื่องมือ · ตรวจงาน · ส่งมอบ</p>
        </div>
        <span className={`status ${health === 'checking' ? '' : health}`} role="status">
          {health === 'checking' ? 'กำลังตรวจ Hub…' : health === 'ok' ? 'Hub พร้อมใช้งาน' : 'ติดต่อ Hub ไม่ได้'}
        </span>
      </header>
      {health === 'down' && (
        <p className="notice error" role="alert">
          ติดต่อ Production Hub ไม่ได้ — เปิดด้วย <code>python -m production_hub.api</code> ในโฟลเดอร์ services/production-hub แล้วรีเฟรช
        </p>
      )}
      <nav className="tabs" role="tablist" aria-label="ส่วนของ Console">
        <button type="button" role="tab" aria-selected={tab === 'library'} onClick={() => setTab('library')}>คลังไฟล์</button>
        <button type="button" role="tab" aria-selected={tab === 'projects'} onClick={() => setTab('projects')}>โปรเจกต์</button>
      </nav>
      {tab === 'library' ? <Library registry={registry} /> : <Projects />}
    </main>
  );
}
