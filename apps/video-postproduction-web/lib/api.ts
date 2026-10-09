export type ProjectStatus = "created" | "processing" | "ready_for_edit" | "failed";
export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "retry_queued";
export type TranscriptionQuality = "fast" | "accurate";

export interface Project {
  id: string;
  name: string;
  instructions: string | null;
  transcription_quality: TranscriptionQuality;
  status: ProjectStatus | string;
  created_at: string;
  updated_at: string;
}

export interface ProjectListResponse {
  items: Project[];
  limit: number;
  offset: number;
  total: number;
}

export interface TranscriptionCapabilities {
  supports_project_quality: boolean;
}

export interface CreateProjectRequest {
  name: string;
  instructions?: string;
  transcription_quality?: TranscriptionQuality;
}

export interface Asset {
  id: string;
  project_id: string;
  original_filename: string;
  media_type: string;
  size_bytes: number;
  status: string;
  metadata: Record<string, unknown>;
  created_at: string;
}

export interface AssetListResponse {
  items: Asset[];
  limit: number;
  offset: number;
  total: number;
}

export interface UploadResponse {
  asset_id: string;
  job_id: string;
  status: JobStatus | string;
  poll_url: string;
}

export interface Job {
  id: string;
  project_id: string;
  asset_id: string;
  kind: string;
  status: JobStatus | string;
  progress: number;
  error_code: string | null;
  error_message: string | null;
  current_stage: string | null;
  stage_status: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TranscriptWord {
  text: string;
  start: number;
  end: number;
  confidence: number | null;
}

export interface TranscriptSegment {
  id: string;
  start: number;
  end: number;
  text: string;
  words: TranscriptWord[];
}

export interface Transcript {
  id: string;
  project_id: string;
  asset_id: string;
  provider: string;
  model: string;
  language: string | null;
  duration: number | null;
  normalized: {
    schema_version?: string;
    segments?: TranscriptSegment[];
    [key: string]: unknown;
  };
  created_at: string;
}

export interface EDL {
  id: string;
  project_id: string;
  source_asset_id: string | null;
  version: number;
  schema_version: string;
  document: Record<string, unknown>;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface RenderOutput {
  id: string;
  project_id: string;
  edl_id: string;
  job_id: string | null;
  status: string;
  metadata: Record<string, unknown>;
  error: string | null;
  created_at: string;
  updated_at: string;
}

interface APIErrorPayload {
  error?: {
    code?: string;
    message?: string;
    request_id?: string;
  };
}

export class APIClientError extends Error {
  readonly code: string;
  readonly status: number;
  readonly requestId?: string;

  constructor(code: string, message: string, status: number, requestId?: string) {
    super(`${code}: ${message}`);
    this.name = "APIClientError";
    this.code = code;
    this.status = status;
    this.requestId = requestId;
  }
}

type Fetcher = typeof fetch;

const API_ORIGIN = process.env.NEXT_PUBLIC_VIDEO_POSTPRODUCTION_API_ORIGIN ?? "http://127.0.0.1:8000";

const apiURL = (path: string): string =>
  path.startsWith("http://") || path.startsWith("https://")
    ? path
    : `${API_ORIGIN.replace(/\/$/, "")}${path}`;

async function parseJSON<T>(response: Response): Promise<T> {
  const text = await response.text();
  if (!text) {
    return undefined as T;
  }
  return JSON.parse(text) as T;
}

async function request<T>(path: string, init: RequestInit, fetcher: Fetcher): Promise<T> {
  let response: Response;
  try {
    response = await fetcher(apiURL(path), init);
  } catch {
    throw new APIClientError("network_error", "The video service could not be reached.", 0);
  }
  if (!response.ok) {
    let payload: APIErrorPayload = {};
    try {
      payload = await parseJSON<APIErrorPayload>(response);
    } catch {
      // Preserve a safe generic error when the service did not return JSON.
    }
    throw new APIClientError(
      payload.error?.code ?? "http_error",
      payload.error?.message ?? "The request could not be completed.",
      response.status,
      payload.error?.request_id,
    );
  }
  try {
    return await parseJSON<T>(response);
  } catch {
    throw new APIClientError("invalid_response", "The video service returned invalid data.", response.status);
  }
}

export function listProjects(fetcher: Fetcher = fetch): Promise<ProjectListResponse> {
  return request<ProjectListResponse>("/api/v1/projects", {method: "GET"}, fetcher);
}

export function getTranscriptionCapabilities(
  fetcher: Fetcher = fetch,
): Promise<TranscriptionCapabilities> {
  return request<unknown>("/api/v1/transcription/capabilities", {method: "GET"}, fetcher).then((payload) => {
    if (
      typeof payload !== "object" ||
      payload === null ||
      !("supports_project_quality" in payload) ||
      typeof payload.supports_project_quality !== "boolean"
    ) {
      throw new APIClientError(
        "invalid_response",
        "The video service returned invalid transcription capabilities.",
        200,
      );
    }
    return {supports_project_quality: payload.supports_project_quality};
  });
}

export function createProject(
  payload: CreateProjectRequest,
  fetcher: Fetcher = fetch,
): Promise<Project> {
  return request<Project>("/api/v1/projects", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(payload),
  }, fetcher);
}

export function retranscribeAccurately(
  projectId: string,
  assetId: string,
  fetcher: Fetcher = fetch,
): Promise<Job> {
  return request<Job>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}/transcribe-accurately`,
    {method: "POST"},
    fetcher,
  );
}

export function getProject(projectId: string, fetcher: Fetcher = fetch): Promise<Project> {
  return request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, {method: "GET"}, fetcher);
}

export function uploadAsset(
  projectId: string,
  file: File,
  fetcher: Fetcher = fetch,
): Promise<UploadResponse> {
  const body = new FormData();
  body.append("file", file, file.name);
  return request<UploadResponse>(`/api/v1/projects/${encodeURIComponent(projectId)}/assets`, {
    method: "POST",
    body,
  }, fetcher);
}

export function getAsset(projectId: string, assetId: string, fetcher: Fetcher = fetch): Promise<Asset> {
  return request<Asset>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}`,
    {method: "GET"},
    fetcher,
  );
}

export function listAssets(projectId: string, fetcher: Fetcher = fetch): Promise<AssetListResponse> {
  return request<AssetListResponse>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/assets`,
    {method: "GET"},
    fetcher,
  );
}

export function getJob(jobId: string, fetcher: Fetcher = fetch): Promise<Job> {
  return request<Job>(`/api/v1/jobs/${encodeURIComponent(jobId)}`, {method: "GET"}, fetcher);
}

export function getLatestJob(projectId: string, fetcher: Fetcher = fetch): Promise<Job> {
  return request<Job>(
    `/api/v1/projects/${encodeURIComponent(projectId)}/jobs/latest`,
    {method: "GET"},
    fetcher,
  );
}

export function retryJob(jobId: string, stage = "transcribe", fetcher: Fetcher = fetch): Promise<Job> {
  return request<Job>(`/api/v1/jobs/${encodeURIComponent(jobId)}/retry`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({stage}),
  }, fetcher);
}

export function getTranscript(projectId: string, fetcher: Fetcher = fetch): Promise<Transcript> {
  return request<Transcript>(`/api/v1/projects/${encodeURIComponent(projectId)}/transcript`, {method: "GET"}, fetcher);
}

export function getEDL(projectId: string, fetcher: Fetcher = fetch): Promise<EDL> {
  return request<EDL>(`/api/v1/projects/${encodeURIComponent(projectId)}/edl`, {method: "GET"}, fetcher);
}

export function updateEDL(
  projectId: string,
  document: Record<string, unknown>,
  fetcher: Fetcher = fetch,
): Promise<EDL> {
  return request<EDL>(`/api/v1/projects/${encodeURIComponent(projectId)}/edl`, {
    method: "PATCH",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(document),
  }, fetcher);
}

export function enqueueRender(
  projectId: string,
  edlId?: string,
  fetcher: Fetcher = fetch,
): Promise<RenderOutput> {
  return request<RenderOutput>(`/api/v1/projects/${encodeURIComponent(projectId)}/renders`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(edlId ? {edl_id: edlId} : {}),
  }, fetcher);
}

export function getRender(renderId: string, fetcher: Fetcher = fetch): Promise<RenderOutput> {
  return request<RenderOutput>(`/api/v1/renders/${encodeURIComponent(renderId)}`, {method: "GET"}, fetcher);
}

export function getAssetStreamURL(projectId: string, assetId: string): string {
  return apiURL(
    `/api/v1/projects/${encodeURIComponent(projectId)}/assets/${encodeURIComponent(assetId)}/stream`,
  );
}

export function getRenderStreamURL(renderId: string): string {
  return apiURL(`/api/v1/renders/${encodeURIComponent(renderId)}/stream`);
}

export function getRenderDownloadURL(renderId: string): string {
  return apiURL(`/api/v1/renders/${encodeURIComponent(renderId)}/download`);
}
