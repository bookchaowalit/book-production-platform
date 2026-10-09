import { describe, expect, it } from "vitest";

import {
  createProject,
  getLatestJob,
  getTranscript,
  getTranscriptionCapabilities,
  listAssets,
  retranscribeAccurately,
  uploadAsset,
} from "./api";

const uploadResponse = {
  asset_id: "asset-1",
  job_id: "job-1",
  status: "queued",
  poll_url: "/api/v1/jobs/job-1",
};

const fetcherReturning = (body: unknown, status = 200): typeof fetch =>
  (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;

describe("video post-production API client", () => {
  it("creates projects with the selected transcription quality", async () => {
    let requestBody = "";
    const result = await createProject(
      {name: "Careful demo", transcription_quality: "accurate"},
      async (_input, init) => {
        requestBody = String(init?.body);
        return new Response(JSON.stringify({id: "project-1", transcription_quality: "accurate"}));
      },
    );

    expect(JSON.parse(requestBody)).toEqual({name: "Careful demo", transcription_quality: "accurate"});
    expect(result.transcription_quality).toBe("accurate");
  });

  it("creates projects without overriding the configured provider model", async () => {
    let requestBody = "";
    await createProject({name: "Provider default"}, async (_input, init) => {
      requestBody = String(init?.body);
      return new Response(JSON.stringify({id: "project-2"}));
    });

    expect(JSON.parse(requestBody)).toEqual({name: "Provider default"});
  });

  it("AC-VID-01c loads provider quality capabilities", async () => {
    const result = await getTranscriptionCapabilities(async () =>
      new Response(JSON.stringify({supports_project_quality: false})),
    );

    expect(result.supports_project_quality).toBe(false);
  });

  it("AC-VID-01c fails closed on malformed provider capabilities", async () => {
    await expect(
      getTranscriptionCapabilities(fetcherReturning({supports_project_quality: "false"})),
    ).rejects.toThrow("invalid_response");
  });

  it("requests accurate re-transcription for the selected asset", async () => {
    let requestedURL = "";
    let requestedMethod = "";
    const result = await retranscribeAccurately("project 1", "asset 1", async (input, init) => {
      requestedURL = String(input);
      requestedMethod = String(init?.method);
      return new Response(JSON.stringify({id: "job-2", status: "queued"}), {status: 202});
    });

    expect(requestedURL).toContain("/projects/project%201/assets/asset%201/transcribe-accurately");
    expect(requestedMethod).toBe("POST");
    expect(result.id).toBe("job-2");
  });

  it("parses an accepted upload and exposes the poll URL", async () => {
    const result = await uploadAsset(
      "project-1",
      new File(["video"], "sample.mp4", { type: "video/mp4" }),
      fetcherReturning(uploadResponse, 202),
    );

    expect(result.status).toBe("queued");
    expect(result.poll_url).toBe("/api/v1/jobs/job-1");
  });

  it("turns the API error envelope into a user-safe Error", async () => {
    await expect(
      getTranscript(
        "project-1",
        fetcherReturning(
          {error: {code: "transcript_not_ready", message: "The transcript is not ready yet."}},
          409,
        ),
      ),
    ).rejects.toThrow("transcript_not_ready");
  });

  it("discovers existing project assets and the latest job", async () => {
    const assets = await listAssets(
      "project-1",
      fetcherReturning({
        items: [
          {
            id: "asset-1",
            project_id: "project-1",
            original_filename: "sample.mp4",
            media_type: "video/mp4",
            size_bytes: 5,
            status: "uploaded",
            metadata: {},
            created_at: "2026-09-22T00:00:00Z",
          },
        ],
        limit: 50,
        offset: 0,
        total: 1,
      }),
    );
    const job = await getLatestJob(
      "project-1",
      fetcherReturning({id: "job-1", project_id: "project-1", asset_id: "asset-1", status: "queued"}),
    );

    expect(assets.items[0]?.id).toBe("asset-1");
    expect(job.id).toBe("job-1");
  });
});
