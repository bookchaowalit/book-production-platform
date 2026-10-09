const path = require("node:path");
const electron = require("electron");
const { startDesktopApp } = require("../src/main");

const packageRoot = path.resolve(__dirname, "..");
const stoppedStatus = {
  phase: "stopped",
  action: null,
  code: null,
  message: "Video Studio ยังไม่ได้เริ่มทำงาน",
  services: { postgres: "unknown", redis: "unknown", api: "unknown", worker: "unknown", web: "unknown" },
};

let finished = false;
function finish(code, detail) {
  if (finished) return;
  finished = true;
  console.log(detail);
  setTimeout(() => electron.app.exit(code), 50);
}

const app = new Proxy(electron.app, {
  get(target, property) {
    if (property === "isPackaged") return false;
    if (property === "getAppPath") return () => packageRoot;
    const value = Reflect.get(target, property, target);
    return typeof value === "function" ? value.bind(target) : value;
  },
});

class HiddenBrowserWindow extends electron.BrowserWindow {
  show() {}

  constructor(options) {
    super({ ...options, show: false });
    this.webContents.once("did-finish-load", () => { void inspectLauncher(this); });
  }
}

async function inspectLauncher(window) {
  let state;
  try {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      state = await window.webContents.executeJavaScript(`({
        title: document.getElementById("statusTitle")?.textContent,
        summary: document.getElementById("serviceSummary")?.textContent,
        startVisible: !document.getElementById("startButton")?.hidden,
        bridgeType: typeof window.videoStudio?.getStatus
      })`);
      if (state.title === "บริการหยุดอยู่" && state.startVisible && state.bridgeType === "function") {
        finish(0, "PASS: sandboxed launcher preload, IPC, and rendered status");
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    finish(1, `FAIL: launcher remained at ${JSON.stringify(state)}`);
  } catch (error) {
    finish(1, `FAIL: launcher inspection failed: ${error instanceof Error ? error.message : "unknown error"}`);
  }
}

const fakeManager = {
  start: async () => stoppedStatus,
  stop: async () => stoppedStatus,
  recover: async () => stoppedStatus,
};

startDesktopApp({
  app,
  BrowserWindow: HiddenBrowserWindow,
  dialog: electron.dialog,
  ipcMain: electron.ipcMain,
  Menu: electron.Menu,
  protocol: electron.protocol,
  session: electron.session,
  shell: electron.shell,
}, {
  argv: ["electron", "smoke-launcher"],
  platform: process.platform,
  appUserModelIdAlreadySet: true,
  createManager: () => fakeManager,
});

setTimeout(() => finish(1, "FAIL: launcher did not finish loading"), 10000);
