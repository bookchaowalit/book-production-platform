(function () {
  /** @typedef {import("../runtime/types").RuntimePhase} RuntimePhase */
  /** @typedef {import("../runtime/types").RuntimeCode} RuntimeCode */
  /** @typedef {import("../runtime/types").ServiceName} ServiceName */
  /** @typedef {import("../runtime/types").ServiceState} ServiceState */
  /** @typedef {import("../runtime/types").RuntimeStatus} RuntimeStatus */

  /** @type {Record<RuntimePhase, string>} */
  const phaseLabels = {
    checking: "ตรวจสอบระบบ",
    starting: "กำลังเริ่มบริการ",
    "waiting-health": "เตรียมพื้นที่ทำงาน",
    ready: "พร้อมใช้งาน",
    "needs-action": "ต้องตรวจสอบก่อน",
    stopped: "บริการหยุดอยู่",
    failed: "เริ่มใช้งานไม่สำเร็จ",
  };
  /** @type {Partial<Record<Exclude<RuntimeCode, null>, [string, string]>>} */
  const codeMessages = {
    "missing-docker": ["ต้องติดตั้ง Docker Desktop", "ติดตั้ง Docker Desktop แล้วเปิดแอป จากนั้นลองอีกครั้ง"],
    "engine-unavailable": ["เปิด Docker Desktop ก่อน", "ตรวจสอบว่า Docker ใช้ Linux containers และพร้อมทำงานแล้ว"],
    "port-conflict": ["พอร์ตของแอปถูกใช้งานอยู่", "ปิดบริการอื่นที่ใช้พอร์ต 8000 หรือ 3400 แล้วลองใหม่ แอปจะไม่ปิดโปรเซสอื่นให้"],
    "partial-runtime": ["พบบริการที่ทำงานไม่ครบ", "ตรวจสอบงานที่กำลังประมวลผลก่อนเลือกกู้คืนบริการ"],
    "compose-failed": ["Docker Compose ทำงานไม่สำเร็จ", "ตรวจสอบ Docker Desktop แล้วลองเริ่มใหม่ ข้อมูลเดิมยังคงอยู่"],
    "health-timeout": ["บริการใช้เวลาเตรียมนานกว่าปกติ", "ระบบเดิมยังคงอยู่ ลองตรวจสอบสถานะแล้วเริ่มใหม่อีกครั้ง"],
    "invalid-runtime": ["อ่านสถานะบริการไม่สำเร็จ", "ลองปิดและเปิดแอปใหม่ หากยังพบปัญหาให้ตรวจสอบ Docker Desktop"],
  };
  /** @type {Record<ServiceState, string>} */
  const serviceLabels = {
    unknown: "รอตรวจสอบ",
    running: "กำลังทำงาน",
    healthy: "พร้อม",
    stopped: "หยุดแล้ว",
    unhealthy: "มีปัญหา",
  };
  /** @type {ServiceName[]} */
  const serviceNames = ["postgres", "redis", "api", "worker", "web"];
  /** @param {string} id @returns {HTMLElement} */
  function requireElement(id) {
    const element = document.getElementById(id);
    if (!element) throw new Error("Launcher element is unavailable");
    return element;
  }

  const phaseLabel = requireElement("phaseLabel");
  const statusTitle = requireElement("statusTitle");
  const statusCopy = requireElement("statusCopy");
  const statusSymbol = requireElement("statusSymbol");
  const serviceSummary = requireElement("serviceSummary");
  const startButton = /** @type {HTMLButtonElement} */ (requireElement("startButton"));
  const recoverButton = /** @type {HTMLButtonElement} */ (requireElement("recoverButton"));
  const dockerHelp = requireElement("dockerHelp");
  /** @type {RuntimeStatus | null} */
  let latestStatus = null;

  /** @param {RuntimeStatus} status */
  function render(status) {
    if (!status || typeof status !== "object") return;
    latestStatus = status;

    const phase = status.phase;
    const codeMessage = status.code ? codeMessages[status.code] ?? null : null;
    phaseLabel.textContent = phaseLabels[phase];
    statusTitle.textContent = codeMessage ? codeMessage[0] : phase === "ready" ? "เปิดพื้นที่ทำงานของคุณ" : phaseLabels[phase];
    statusCopy.textContent = codeMessage ? codeMessage[1] : phase === "ready"
      ? "บริการพร้อมแล้ว กำลังเปิด Video Studio"
      : phase === "stopped"
        ? "เริ่มบริการเมื่อคุณพร้อม งานและข้อมูลเดิมจะไม่ถูกลบ"
        : phase === "waiting-health"
          ? "กำลังรอ API และหน้า Studio ให้พร้อมใช้งาน"
          : phase === "starting"
            ? "การเริ่มครั้งแรกอาจใช้เวลาและต้องเชื่อมต่ออินเทอร์เน็ต"
            : "กำลังตรวจสอบ Docker Desktop และบริการของ Video Studio";
    statusSymbol.dataset.phase = phase;
    statusSymbol.dataset.error = codeMessage ? "true" : "false";

    const services = status.services;
    let readyCount = 0;
    for (const name of serviceNames) {
      const row = /** @type {HTMLElement | null} */ (document.querySelector(`[data-service="${name}"]`));
      if (!row) continue;
      const stateLabel = /** @type {HTMLElement | null} */ (row.querySelector(".service-state"));
      const state = serviceLabels[services[name]] ? services[name] : "unknown";
      row.dataset.state = state;
      if (stateLabel) stateLabel.textContent = serviceLabels[state];
      if (state === "healthy") readyCount += 1;
    }
    serviceSummary.textContent = phase === "ready" ? "พร้อมใช้งาน" : `${readyCount} / ${serviceNames.length} พร้อม`;
    startButton.hidden = phase === "checking" || phase === "needs-action" || phase === "starting" || phase === "waiting-health";
    startButton.disabled = startButton.hidden;
    recoverButton.hidden = phase !== "needs-action" || status.action !== "recover-required";
    recoverButton.disabled = recoverButton.hidden;
    dockerHelp.hidden = status.code !== "missing-docker";
  }

  /** @param {() => Promise<RuntimeStatus>} action */
  async function runAction(action) {
    startButton.disabled = true;
    recoverButton.disabled = true;
    try {
      await action();
    } catch {
      render({
        phase: "failed",
        action: null,
        code: "invalid-runtime",
        message: "",
        services: latestStatus?.services ?? {
          postgres: "unknown", redis: "unknown", api: "unknown", worker: "unknown", web: "unknown",
        },
      });
    } finally {
      if (latestStatus) render(latestStatus);
    }
  }

  startButton.addEventListener("click", () => { void runAction(() => window.videoStudio.start()); });
  recoverButton.addEventListener("click", () => { void runAction(() => window.videoStudio.recover()); });
  window.videoStudio.onStatus(render);
  void window.videoStudio.getStatus().then(render).catch(() => {
    render({
      phase: "failed",
      action: null,
      code: "invalid-runtime",
      message: "",
      services: { postgres: "unknown", redis: "unknown", api: "unknown", worker: "unknown", web: "unknown" },
    });
  });
}());
