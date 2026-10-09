export type QAStatus = 'accepted' | 'rejected' | 'needs_review';
export type ReviewStatus = 'draft' | 'in_review' | 'changes_requested' | 'approved' | 'delivered';

export interface ArtifactRow {
  artifact_id: string;
  kind: string;
  filename: string;
  byte_size: number;
  tool_id: string;
  job_id: string;
  project_id: string | null;
  qa_status: QAStatus;
  created_at: string;
}

export interface SendOption {
  handoff_id: string;
  to_tool: string;
  status: 'implemented' | 'planned';
  available: boolean;
  required_params: string[];
  extra_kinds: string[];
}

export interface ArtifactRecord {
  artifact_id: string;
  kind: string;
  media_type: string;
  byte_size: number;
  filename: string;
  producer: { tool_id: string; tool_version: string; job_id: string; recipe_version?: string };
  lineage: string[];
  qa: { status: QAStatus };
  privacy_class: string;
  project_id?: string;
  attributes?: Record<string, unknown>;
  created_at: string;
  used_by: string[];
  sends: SendOption[];
}

export interface LineageNode {
  artifact_id: string;
  kind: string;
  filename: string;
  producer: { tool_id: string };
  inputs?: LineageNode[];
  inputs_truncated?: number;
}

export interface ProjectSummary {
  project_id: string;
  name: string;
  client: string | null;
  status: string;
  artifacts: number;
  approved: number | null;
  created_at: string;
}

export interface ProjectArtifact {
  artifact_id: string;
  kind: string;
  filename: string;
  byte_size: number;
  tool_id: string;
  qa_status: QAStatus;
  privacy_class: string;
  review_status: ReviewStatus;
  updated_at: string;
}

export interface Registry {
  kinds: Record<string, { description: string; media_types: string[] }>;
  tools: Record<string, { produces: string[]; consumes: string[] }>;
}

export class HubError extends Error {
  constructor(public code: string, message: string, public status: number) {
    super(message);
  }
}

type Fetcher = typeof fetch;

async function call<T>(path: string, init: RequestInit = {}, fetcher: Fetcher = fetch): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(`/api/hub/${path}`, { cache: 'no-store', ...init });
  } catch {
    throw new HubError('network_error', 'เชื่อมต่อ Console ไม่ได้ ลองรีเฟรชหน้า', 0);
  }
  let payload: unknown = null;
  try {
    payload = await response.json();
  } catch {
    throw new HubError('invalid_response', 'Production Hub ตอบกลับในรูปแบบที่อ่านไม่ได้', response.status);
  }
  if (!response.ok) {
    const error = (payload as { error?: { code?: string; message?: string } } | null)?.error;
    throw new HubError(error?.code ?? 'http_error', error?.message ?? 'ทำรายการไม่สำเร็จ', response.status);
  }
  return payload as T;
}

const post = <T>(path: string, body: unknown, fetcher?: Fetcher) =>
  call<T>(path, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } }, fetcher);

export const api = {
  health: (f?: Fetcher) => call<{ status: string }>('v1/health', {}, f),
  registry: (f?: Fetcher) => call<Registry>('v1/registry', {}, f),
  artifacts: (filters: { kind?: string; tool?: string; project?: string }, f?: Fetcher) => {
    const query = new URLSearchParams(Object.entries(filters).filter(([, value]) => value) as [string, string][]);
    return call<{ items: ArtifactRow[] }>(`v1/artifacts${query.size ? `?${query}` : ''}`, {}, f);
  },
  artifact: (id: string, f?: Fetcher) => call<ArtifactRecord>(`v1/artifacts/${id}`, {}, f),
  lineage: (id: string, f?: Fetcher) => call<LineageNode>(`v1/artifacts/${id}/lineage`, {}, f),
  contentUrl: (id: string) => `/api/hub/v1/artifacts/${id}/content`,
  projects: (f?: Fetcher) => call<{ items: ProjectSummary[] }>('v1/projects', {}, f),
  project: (id: string, f?: Fetcher) =>
    call<{ project: ProjectSummary; artifacts: ProjectArtifact[] }>(`v1/projects/${id}`, {}, f),
  createProject: (body: { project_id: string; name: string; client?: string }, f?: Fetcher) =>
    post<{ created: boolean }>('v1/projects', body, f),
  review: (project: string, body: { artifact_id: string; status: ReviewStatus; comment?: string }, f?: Fetcher) =>
    post<{ from: ReviewStatus; to: ReviewStatus }>(`v1/projects/${project}/review`, body, f),
  packageProject: (project: string, f?: Fetcher) =>
    post<{ artifact_id: string; path: string; files: number }>(`v1/projects/${project}/package`, {}, f),
  send: (body: { artifact_id: string; to: string; params: Record<string, string>; with?: string[] }, f?: Fetcher) =>
    post<{ send_id: string; handoff_id: string; outputs: { artifact_id: string; kind: string; filename: string }[] }>(
      'v1/send', body, f,
    ),
};

export const REVIEW_LABELS: Record<ReviewStatus, string> = {
  draft: 'ร่าง',
  in_review: 'รอตรวจ',
  changes_requested: 'ขอแก้ไข',
  approved: 'อนุมัติแล้ว',
  delivered: 'ส่งมอบแล้ว',
};

export const NEXT_REVIEW: Record<ReviewStatus, ReviewStatus[]> = {
  draft: ['in_review', 'approved'],
  in_review: ['approved', 'changes_requested'],
  changes_requested: ['draft', 'in_review'],
  approved: ['delivered', 'changes_requested'],
  delivered: [],
};

export const QA_LABELS: Record<QAStatus, string> = {
  accepted: 'ผ่าน QA',
  rejected: 'ไม่ผ่าน QA',
  needs_review: 'รอตรวจ QA',
};

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 ? 0 : 1)} ${units[unit]}`;
}

export const shortId = (id: string): string => id.slice(7, 19);

export const isPreviewable = (mediaType: string): boolean =>
  mediaType.startsWith('image/') || mediaType === 'application/pdf';
