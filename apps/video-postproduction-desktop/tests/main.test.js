const assert = require("node:assert/strict");
const test = require("node:test");

const { startDesktopApp } = require("../src/main");

function createElectron({ lock = true, deferReady = false } = {}) {
  /** @type {string[]} */
  const calls = [];
  /** @type {any[][]} */
  const menuTemplates = [];
  /** @type {string[]} */
  const openedExternal = [];
  const electron = {
    app: {
      requestSingleInstanceLock: () => {
        calls.push("lock");
        return lock;
      },
      on: () => {},
      quit: () => calls.push("quit"),
      setAppUserModelId: (/** @type {string} */ id) => calls.push(`app-id:${id}`),
      whenReady: () => deferReady ? new Promise(() => {}) : Promise.resolve(),
      getAppPath: () => "/repo/apps/video-postproduction-desktop",
      getPath: () => "/user-data",
      isPackaged: false,
    },
    protocol: {
      registerSchemesAsPrivileged: () => calls.push("register-scheme"),
    },
    ipcMain: { handle: () => {} },
    Menu: {
      buildFromTemplate: (/** @type {any[]} */ template) => {
        menuTemplates.push(template);
        return {};
      },
      setApplicationMenu: () => {},
    },
    shell: {
      openExternal: (/** @type {string} */ url) => {
        openedExternal.push(url);
        return Promise.resolve();
      },
    },
  };
  return { calls, electron, menuTemplates, openedExternal };
}

test("Squirrel startup exits before locking or creating app resources", () => {
  const fake = createElectron();
  let managerCreated = false;

  const started = startDesktopApp(fake.electron, {
    argv: ["video-studio.exe", "--squirrel-install"],
    platform: "win32",
    createManager: () => {
      managerCreated = true;
      throw new Error("manager must not be created during Squirrel startup");
    },
  });

  assert.equal(started, false);
  assert.equal(managerCreated, false);
  assert.ok(fake.calls.includes("app-id:com.squirrel.SoloEmpireVideoStudio.SoloEmpireVideoStudio"));
  assert.deepEqual(fake.calls.slice(-1), ["quit"]);
  assert.equal(fake.calls.includes("lock"), false);
  assert.equal(fake.calls.includes("register-scheme"), false);
});

test("a second app instance quits before runtime manager creation", () => {
  const fake = createElectron({ lock: false });
  let managerCreated = false;

  const started = startDesktopApp(fake.electron, {
    argv: ["video-studio.exe"],
    platform: "win32",
    createManager: () => {
      managerCreated = true;
      throw new Error("manager must not be created after a denied instance lock");
    },
  });

  assert.equal(started, false);
  assert.equal(managerCreated, false);
  assert.deepEqual(fake.calls, ["app-id:com.squirrel.SoloEmpireVideoStudio.SoloEmpireVideoStudio", "lock", "quit"]);
});

test("Docker installation help opens only through an explicit native menu action", () => {
  const fake = createElectron({ deferReady: true });

  assert.equal(startDesktopApp(fake.electron, {
    argv: ["video-studio.exe"],
    platform: "win32",
    createManager: () => /** @type {any} */ ({ start: async () => ({}) }),
  }), true);

  const menuItems = fake.menuTemplates[0][0].submenu;
  const helpItem = menuItems.find((/** @type {any} */ item) => item.label === "วิธีติดตั้ง Docker Desktop");
  assert.ok(helpItem);
  helpItem.click();
  assert.deepEqual(fake.openedExternal, [
    "https://docs.docker.com/desktop/setup/install/windows-install/",
  ]);
});

test("main IPC rejects an otherwise trusted launcher loaded from a non-allowlisted path", async () => {
  /** @type {() => void} */
  let markReady = () => {};
  const ready = new Promise((resolve) => { markReady = () => resolve(undefined); });
  /** @type {Map<string, any>} */
  const handlers = new Map();
  /** @type {any[]} */
  const windows = [];
  /** @type {any[][]} */
  const menuTemplates = [];
  /** @type {string[]} */
  const openedExternal = [];
  let nextWebContentsId = 0;
  class FakeBrowserWindow {
    /** @param {any} options */
    constructor(options) {
      this.options = options;
      this.navigationListeners = new Map();
      this.webContents = {
        id: ++nextWebContentsId,
        send: () => {},
        on: (/** @type {string} */ event, /** @type {any} */ listener) => {
          this.navigationListeners.set(event, listener);
        },
        setWindowOpenHandler: (/** @type {any} */ handler) => { this.windowOpenHandler = handler; },
      };
      windows.push(this);
    }
    /** @param {string} _event @param {(...args: any[]) => void} _listener */
    on(_event, _listener) {}
    /** @param {string} _event @param {(...args: any[]) => void} _listener */
    once(_event, _listener) {}
    /** @param {string} _url */
    loadURL(_url) { return Promise.resolve(); }
    isDestroyed() { return false; }
    isMinimized() { return false; }
    restore() {}
    show() {}
    hide() {}
    focus() {}
    close() {}
  }
  /** @type {import("../src/runtime/types").RuntimeStatus} */
  const status = {
    phase: "needs-action",
    action: "recover-required",
    code: "partial-runtime",
    message: "ตรวจสอบบริการก่อนกู้คืน",
    services: { postgres: "unknown", redis: "unknown", api: "unknown", worker: "unknown", web: "unknown" },
  };
  const electron = {
    app: {
      requestSingleInstanceLock: () => true,
      on: () => {},
      quit: () => {},
      setAppUserModelId: () => {},
      whenReady: () => ready,
      getAppPath: () => "/repo/apps/video-postproduction-desktop",
      getPath: () => "/user-data",
      isPackaged: false,
    },
    BrowserWindow: FakeBrowserWindow,
    dialog: { showMessageBox: async () => ({ response: 1 }) },
    ipcMain: {
      handle: (/** @type {string} */ channel, /** @type {any} */ handler) => handlers.set(channel, handler),
    },
    Menu: {
      buildFromTemplate: (/** @type {any[]} */ template) => {
        menuTemplates.push(template);
        return {};
      },
      setApplicationMenu: () => {},
    },
    protocol: { registerSchemesAsPrivileged: () => {}, handle: () => {} },
    session: {
      defaultSession: {
        webRequest: { onHeadersReceived: () => {} },
        setPermissionRequestHandler: () => {},
      },
    },
    shell: { openExternal: async (/** @type {string} */ url) => { openedExternal.push(url); } },
  };

  startDesktopApp(electron, {
    argv: ["video-studio.exe"],
    platform: "win32",
    createManager: ({ publishStatus }) => /** @type {any} */ ({
      start: async () => { publishStatus(status); return status; },
      recover: async () => status,
      stop: async () => status,
    }),
  });
  markReady();
  await new Promise((resolve) => setImmediate(resolve));

  const launcher = windows[0];
  assert.ok(launcher);
  const getStatus = handlers.get("video-studio:get-status");
  const invalidPathFrame = {
    origin: "solo-video://launcher",
    url: "solo-video://launcher/secret.txt",
  };
  launcher.webContents.mainFrame = invalidPathFrame;
  const invalidPathEvent = {
    sender: launcher.webContents,
    senderFrame: invalidPathFrame,
  };
  await assert.rejects(Promise.resolve().then(() => getStatus(invalidPathEvent)), /Unauthorized/);

  const launcherDocumentFrame = {
    origin: "solo-video://launcher",
    url: "solo-video://launcher/index.html",
  };
  launcher.webContents.mainFrame = launcherDocumentFrame;
  const launcherDocumentEvent = {
    sender: launcher.webContents,
    senderFrame: launcherDocumentFrame,
  };
  assert.equal(await getStatus(launcherDocumentEvent), status);
  await assert.rejects(Promise.resolve().then(() => getStatus({
    sender: launcher.webContents,
    senderFrame: { ...launcherDocumentFrame },
  })), /Unauthorized/);

  const dockerGuide = "https://docs.docker.com/desktop/setup/install/windows-install/";
  let navigationPrevented = false;
  launcher.navigationListeners.get("will-navigate")(
    { preventDefault: () => { navigationPrevented = true; } },
    dockerGuide,
  );
  assert.equal(navigationPrevented, true);
  assert.deepEqual(openedExternal, []);
  assert.deepEqual(launcher.windowOpenHandler({ url: dockerGuide }), { action: "deny" });
  assert.deepEqual(openedExternal, []);

  const menuItems = menuTemplates[0][0].submenu;
  const helpItem = menuItems.find((/** @type {any} */ item) => item.label === "วิธีติดตั้ง Docker Desktop");
  assert.ok(helpItem);
  helpItem.click();
  assert.deepEqual(openedExternal, [dockerGuide]);
});

test("default Electron session is not accessed before app readiness", () => {
  let ready = false;
  let sessionAccessBeforeReady = false;
  const electron = {
    app: {
      requestSingleInstanceLock: () => true,
      on: () => {},
      quit: () => {},
      whenReady: () => new Promise(() => {}),
    },
    protocol: { registerSchemesAsPrivileged: () => {} },
    ipcMain: { handle: () => {} },
    Menu: { buildFromTemplate: () => ({}), setApplicationMenu: () => {} },
    get session() {
      return {
        get defaultSession() {
          if (!ready) sessionAccessBeforeReady = true;
          return {
            webRequest: { onHeadersReceived: () => {} },
            setPermissionRequestHandler: () => {},
          };
        },
      };
    },
  };

  assert.equal(startDesktopApp(electron, {
    argv: ["video-studio.exe"],
    platform: "win32",
    createManager: () => /** @type {any} */ ({ start: async () => ({}) }),
  }), true);
  assert.equal(sessionAccessBeforeReady, false);
  ready = true;
});
