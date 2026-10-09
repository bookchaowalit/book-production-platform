import {chromium} from "playwright";

const baseURL = process.env.VIDEO_POSTPRODUCTION_WEB_URL ?? "http://127.0.0.1:3400";
const apiURL = process.env.VIDEO_POSTPRODUCTION_API_URL;
const sampleVideo = process.env.VIDEO_POSTPRODUCTION_SAMPLE_VIDEO;
const uiOnly = !apiURL || !sampleVideo;
const transcriptionTimeoutMs = Number(
  process.env.VIDEO_POSTPRODUCTION_TRANSCRIPTION_TIMEOUT_MS ?? 90 * 60 * 1000,
);

if (!Number.isSafeInteger(transcriptionTimeoutMs) || transcriptionTimeoutMs <= 0) {
  throw new Error("VIDEO_POSTPRODUCTION_TRANSCRIPTION_TIMEOUT_MS must be a positive integer");
}

async function openProjectForm(page) {
  await page.goto(baseURL, {waitUntil: "domcontentloaded"});
  await page.getByRole("heading", {name: "Projects"}).waitFor();
  await page.getByRole("link", {name: /New project/}).click();
  await page.getByRole("heading", {name: /เริ่ม post-production job/}).waitFor();
}

async function transcribeProject(page, name, quality, expectedModel, openForm = true) {
  if (openForm) await openProjectForm(page);
  const qualitySelector = page.getByLabel("คุณภาพการถอดเสียง");
  await qualitySelector.waitFor();
  await qualitySelector.selectOption(quality);
  if (await qualitySelector.inputValue() !== quality) {
    throw new Error(`project transcription quality should be ${quality}`);
  }
  await page.getByLabel("ชื่อโปรเจกต์").fill(name);
  await page.getByLabel("วิดีโอต้นฉบับ").setInputFiles(sampleVideo);
  const startedAt = performance.now();
  await page.getByRole("button", {name: /สร้างและเริ่มประมวลผล/}).click();
  await page.waitForURL(/\/projects\/[^/?#]+(?:[?#]|$)/, {timeout: 30_000});
  await page.getByText(`Transcription model: faster_whisper · ${expectedModel}`, {exact: true})
    .waitFor({timeout: transcriptionTimeoutMs});
  return Math.round(performance.now() - startedAt);
}

const browser = await chromium.launch({headless: true});
try {
  const page = await browser.newPage();
  if (uiOnly) {
    await page.route("**/api/v1/transcription/capabilities", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({supports_project_quality: true}),
      }),
    );
  }
  await page.goto(baseURL, {waitUntil: "domcontentloaded"});
  await page.getByRole("heading", {name: "Projects"}).waitFor();
  await page.getByRole("link", {name: /New project/}).click();
  await page.getByRole("heading", {name: /เริ่ม post-production job/}).waitFor();
  const transcriptionQuality = page.getByLabel("คุณภาพการถอดเสียง");
  await transcriptionQuality.waitFor();
  if (await transcriptionQuality.inputValue() !== "fast") {
    throw new Error("new projects should default to fast transcription");
  }
  await transcriptionQuality.selectOption("accurate");
  if (await transcriptionQuality.inputValue() !== "accurate") {
    throw new Error("accurate transcription should be selectable");
  }
  await transcriptionQuality.selectOption("fast");
  if (uiOnly) {
    const providerDefaultPage = await browser.newPage();
    let projectCreatePayload;
    await providerDefaultPage.route("**/api/v1/projects", (route) => {
      if (route.request().method() !== "POST") return route.continue();
      projectCreatePayload = route.request().postDataJSON();
      return route.fulfill({
        status: 201,
        contentType: "application/json",
        body: JSON.stringify({
          id: "provider-default-smoke",
          name: "Provider default smoke",
          instructions: null,
          transcription_quality: "fast",
          status: "created",
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      });
    });
    await providerDefaultPage.route("**/api/v1/transcription/capabilities", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({supports_project_quality: false}),
      }),
    );
    await providerDefaultPage.goto(baseURL, {waitUntil: "domcontentloaded"});
    await providerDefaultPage.getByRole("link", {name: /New project/}).click();
    await providerDefaultPage.getByRole("heading", {name: /เริ่ม post-production job/}).waitFor();
    await providerDefaultPage.getByText(/ใช้โมเดลที่ตั้งค่าไว้ใน API runtime/).waitFor();
    if (await providerDefaultPage.getByLabel("คุณภาพการถอดเสียง").count() !== 0) {
      throw new Error("providers without project quality support should not expose the quality selector");
    }
    await providerDefaultPage.getByLabel("ชื่อโปรเจกต์").fill("Provider default smoke");
    await providerDefaultPage.getByRole("button", {name: "สร้างโปรเจกต์"}).click();
    await providerDefaultPage.waitForURL(/\/projects\/provider-default-smoke/);
    if (!projectCreatePayload || Object.hasOwn(projectCreatePayload, "transcription_quality")) {
      throw new Error("AC-VID-01c: unsupported providers must use their configured model without a quality override");
    }
    await providerDefaultPage.close();

    // AC-VID-01d: failed capability lookup must block submission and remain retryable.
    const unavailableProviderPage = await browser.newPage();
    let capabilityAttempts = 0;
    await unavailableProviderPage.route("**/api/v1/transcription/capabilities", (route) => {
      capabilityAttempts += 1;
      return route.fulfill(capabilityAttempts <= 2
        ? {
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({error: {code: "capabilities_unavailable", message: "Capability lookup failed."}}),
          }
        : {
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({supports_project_quality: true}),
          });
    });
    await unavailableProviderPage.goto(baseURL, {waitUntil: "domcontentloaded"});
    await unavailableProviderPage.getByRole("link", {name: /New project/}).click();
    await unavailableProviderPage.getByRole("heading", {name: /เริ่ม post-production job/}).waitFor();
    await unavailableProviderPage.getByRole("alert").filter({hasText: /capabilities_unavailable/}).waitFor();
    if (!await unavailableProviderPage.getByRole("button", {name: "สร้างโปรเจกต์"}).isDisabled()) {
      throw new Error("project creation should stay disabled when provider capabilities are unavailable");
    }
    await unavailableProviderPage.getByRole("button", {name: "ลองตรวจสอบอีกครั้ง"}).click();
    await unavailableProviderPage.getByLabel("คุณภาพการถอดเสียง").waitFor();
    await unavailableProviderPage.getByLabel("ชื่อโปรเจกต์").fill("Retry smoke project");
    if (await unavailableProviderPage.getByRole("button", {name: "สร้างโปรเจกต์"}).isDisabled()) {
      throw new Error("project creation should become available after capabilities are retried successfully");
    }
    await unavailableProviderPage.close();

    async function verifyAccurateActionVisibility(supported) {
      const editorPage = await browser.newPage();
      const projectId = "00000000-0000-4000-8000-000000000001";
      const assetId = "00000000-0000-4000-8000-000000000002";
      const createdAt = "2026-01-01T00:00:00Z";
      await editorPage.route("**/api/v1/**", async (route) => {
        const path = new URL(route.request().url()).pathname;
        const json = (body, status = 200) =>
          route.fulfill({
            status,
            contentType: "application/json",
            body: JSON.stringify(body),
          });
        if (path === "/api/v1/transcription/capabilities") {
          return json({supports_project_quality: supported});
        }
        if (path === `/api/v1/projects/${projectId}`) {
          return json({
            id: projectId,
            name: "Editor capability smoke",
            instructions: null,
            transcription_quality: "fast",
            status: "ready_for_edit",
            created_at: createdAt,
            updated_at: createdAt,
          });
        }
        if (path === `/api/v1/projects/${projectId}/assets`) {
          const asset = {
            id: assetId,
            project_id: projectId,
            original_filename: "sample.mp4",
            media_type: "video/mp4",
            size_bytes: 128,
            status: "transcribed",
            metadata: {duration: 1, video: {width: 320, height: 240, fps: 30}},
            created_at: createdAt,
          };
          return json({items: [asset], limit: 50, offset: 0, total: 1});
        }
        if (path === `/api/v1/projects/${projectId}/assets/${assetId}`) {
          return json({
            id: assetId,
            project_id: projectId,
            original_filename: "sample.mp4",
            media_type: "video/mp4",
            size_bytes: 128,
            status: "transcribed",
            metadata: {duration: 1, video: {width: 320, height: 240, fps: 30}},
            created_at: createdAt,
          });
        }
        if (path === `/api/v1/projects/${projectId}/transcript`) {
          return json({
            id: "transcript-smoke",
            project_id: projectId,
            asset_id: assetId,
            provider: "faster_whisper",
            model: "small",
            language: "th",
            duration: 1,
            normalized: {segments: []},
            created_at: createdAt,
          });
        }
        if (path === `/api/v1/projects/${projectId}/jobs/latest`) {
          return json({error: {code: "job_not_ready", message: "No job"}}, 404);
        }
        if (path === `/api/v1/projects/${projectId}/edl`) {
          return json({error: {code: "edl_not_ready", message: "No EDL"}}, 404);
        }
        if (path === `/api/v1/projects/${projectId}/assets/${assetId}/stream`) {
          return route.fulfill({status: 200, contentType: "video/mp4", body: ""});
        }
        return route.continue();
      });
      await editorPage.goto(`${baseURL}/projects/${projectId}`, {waitUntil: "domcontentloaded"});
      await editorPage.getByText("Transcription model: faster_whisper · small", {exact: true})
        .waitFor();
      const action = editorPage.getByRole("button", {name: "ถอดเสียงใหม่แบบแม่นยำ"});
      if (supported) {
        await action.waitFor();
      } else if (await action.count() !== 0) {
        throw new Error("providers without project quality support must not expose accurate retranscription");
      }
      await editorPage.close();
    }

    await verifyAccurateActionVisibility(true);
    await verifyAccurateActionVisibility(false);
    console.log("browser smoke passed (UI-only; set VIDEO_POSTPRODUCTION_API_URL and VIDEO_POSTPRODUCTION_SAMPLE_VIDEO for full flow)");
  } else {
    const fastMs = await transcribeProject(page, "Browser smoke fast", "fast", "small", false);
    const retranscribeButton = page.getByRole("button", {name: "ถอดเสียงใหม่แบบแม่นยำ"});
    await retranscribeButton.waitFor({timeout: 30_000});
    const accurateRetranscriptionStartedAt = performance.now();
    await retranscribeButton.click();
    await page.getByText("Transcription model: faster_whisper · medium", {exact: true})
      .waitFor({timeout: transcriptionTimeoutMs});
    const accurateRetranscriptionMs = Math.round(
      performance.now() - accurateRetranscriptionStartedAt,
    );
    const accurateProjectMs = await transcribeProject(
      page,
      "Browser smoke accurate",
      "accurate",
      "medium",
    );
    console.log(
      `browser smoke passed (fast=${fastMs}ms, accurate_retranscription=${accurateRetranscriptionMs}ms, accurate_project=${accurateProjectMs}ms)`,
    );
  }
} finally {
  await browser.close();
}
