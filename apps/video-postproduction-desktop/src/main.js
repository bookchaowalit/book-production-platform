const path = require("node:path");
const { RuntimeManager } = require("./runtime/manager");
const { DockerCli } = require("./runtime/docker-cli");
const { resolveRuntimePaths } = require("./runtime/paths");
const { WEB_ORIGIN } = require("./runtime/constants");
const {
  acquireSingleInstanceLock,
  confirmStopEngine,
  isSquirrelStartup,
} = require("./electron/lifecycle");
const {
  createLauncherProtocolHandler,
  LAUNCHER_SCHEME,
} = require("./protocol/launcher-protocol");
const {
  DOCKER_DESKTOP_INSTALL_URL,
  getApprovedExternalUrl,
  isAllowedNavigation,
  isTrustedLauncherSender,
} = require("./security/window-policy");
const {
  createLauncherWindowOptions,
  createStudioWindowOptions,
} = require("./security/window-options");
const { IPC_CHANNELS } = require("./security/launcher-bridge");
const { withStudioContentSecurityPolicy } = require("./security/studio-csp");

const APP_USER_MODEL_ID = "com.squirrel.SoloEmpireVideoStudio.SoloEmpireVideoStudio";

/** @typedef {import("./runtime/types").RuntimeStatus} RuntimeStatus */
/** @typedef {import("electron").BrowserWindow} BrowserWindow */

/**
 * Electron shell entry point. Electron and manager creation are injectable so startup guards can
 * be verified without launching windows, Docker, or the user's existing runtime.
 *
 * @param {any} electron Electron's main-process API surface.
 * @param {{
 *   argv?: string[],
 *   platform?: NodeJS.Platform,
 *   appUserModelIdAlreadySet?: boolean,
  *   squirrelStartupHandled?: boolean,
 *   createManager?: (options: {publishStatus: (status: RuntimeStatus) => void}) => InstanceType<typeof RuntimeManager>
 * }} [options]
 * @returns {boolean} Whether this process owns the desktop application lifecycle.
 */
function startDesktopApp(electron, options = {}) {
  const {
    app,
    BrowserWindow,
    dialog,
    ipcMain,
    Menu,
    protocol,
    session,
    shell,
  } = electron;
  const argv = options.argv ?? process.argv;
  const platform = options.platform ?? process.platform;

  if (platform === "win32" && !options.appUserModelIdAlreadySet) {
    app.setAppUserModelId?.(APP_USER_MODEL_ID);
  }
  if (options.squirrelStartupHandled) return false;
  if (isSquirrelStartup(argv)) {
    app.quit();
    return false;
  }

  /** @type {BrowserWindow | undefined} */
  let launcherWindow;
  /** @type {BrowserWindow | undefined} */
  let studioWindow;
  /** @type {"quit" | "launcher"} */
  let studioCloseBehavior = "quit";
  /** @type {RuntimeStatus} */
  let currentStatus = {
    phase: "checking",
    action: null,
    code: null,
    message: "กำลังตรวจสอบ Docker และสถานะบริการ",
    services: { postgres: "unknown", redis: "unknown", api: "unknown", worker: "unknown", web: "unknown" },
  };
  let actionInFlight = false;

  if (!acquireSingleInstanceLock({ app, getLauncherWindow: () => launcherWindow })) return false;

  protocol.registerSchemesAsPrivileged([{
    scheme: LAUNCHER_SCHEME,
    privileges: {
      standard: true,
      secure: true,
      supportFetchAPI: true,
    },
  }]);

  /** @param {RuntimeStatus} status */
  function publishStatus(status) {
    currentStatus = status;
    if (launcherWindow && !launcherWindow.isDestroyed()) {
      try {
        launcherWindow.webContents.send(IPC_CHANNELS.status, status);
      } catch {
        // A launcher closing during a status update must not interrupt a runtime transition.
      }
    }
    if (status.phase === "ready") createStudioWindow();
    if (status.phase === "stopped" && studioWindow && !studioWindow.isDestroyed()) {
      studioCloseBehavior = "launcher";
      studioWindow.close();
    }
  }

  const manager = options.createManager
    ? options.createManager({ publishStatus })
    : createRuntimeManager({ app, publishStatus, platform });

  /** @returns {Promise<RuntimeStatus>} */
  async function runManagerAction(/** @type {"start" | "recover" | "stop"} */ action) {
    if (actionInFlight) return currentStatus;
    actionInFlight = true;
    try {
      const status = await manager[action]();
      publishStatus(status);
      return status;
    } catch {
      /** @type {RuntimeStatus} */
      const safeFailure = {
        phase: "failed",
        action: null,
        code: "compose-failed",
        message: "Docker Compose ทำงานไม่สำเร็จ ระบบเดิมยังคงอยู่",
        services: currentStatus.services,
      };
      publishStatus(safeFailure);
      return safeFailure;
    } finally {
      actionInFlight = false;
    }
  }

  function showLauncherWindow() {
    if (!launcherWindow || launcherWindow.isDestroyed()) return;
    if (launcherWindow.isMinimized()) launcherWindow.restore();
    launcherWindow.show();
    launcherWindow.focus();
  }

  function openDockerInstallGuide() {
    const approvedUrl = getApprovedExternalUrl(DOCKER_DESKTOP_INSTALL_URL);
    if (!approvedUrl) return;
    try {
      void shell.openExternal(approvedUrl).catch(() => {});
    } catch {
      // A failed help-link launch must not expose Electron's error details to the renderer.
    }
  }

  /**
   * @param {BrowserWindow | undefined} parentWindow
   * @param {import("electron").MessageBoxOptions} dialogOptions
   */
  function showSafeDialog(parentWindow, dialogOptions) {
    try {
      const parent = parentWindow && !parentWindow.isDestroyed() ? parentWindow : undefined;
      const result = parent
        ? dialog.showMessageBox(parent, dialogOptions)
        : dialog.showMessageBox(dialogOptions);
      void result.catch(() => {});
    } catch {
      // Dialog failures are intentionally not forwarded into renderer-visible text.
    }
  }

  /** @param {"launcher" | "studio"} windowKind */
  function attachNavigationPolicy(
    /** @type {BrowserWindow} */ window,
    /** @type {"launcher" | "studio"} */ windowKind,
  ) {
    window.webContents.on("will-navigate", (event, url) => {
      if (isAllowedNavigation(url, windowKind)) return;
      event.preventDefault();
    });
    window.webContents.on("will-redirect", (event, url) => {
      if (!isAllowedNavigation(url, windowKind)) event.preventDefault();
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  }

  function createLauncherWindow() {
    const window = new BrowserWindow({
      title: "Solo Empire Video Studio",
      width: 620,
      height: 740,
      minWidth: 500,
      minHeight: 620,
      show: false,
      ...createLauncherWindowOptions(path.join(__dirname, "preload.js")),
    });
    launcherWindow = window;
    attachNavigationPolicy(window, "launcher");
    window.once("ready-to-show", () => {
      if (!studioWindow && !window.isDestroyed()) window.show();
    });
    window.on("closed", () => {
      launcherWindow = undefined;
      if (!studioWindow) app.quit();
    });
    void window.loadURL(`${LAUNCHER_SCHEME}://launcher/index.html`).catch(() => {
      if (window.isDestroyed()) return;
      showSafeDialog(window, {
        type: "error",
        title: "เปิด Video Studio ไม่สำเร็จ",
        message: "ไม่สามารถเปิดหน้าต่างเริ่มต้นของแอปได้",
        detail: "ลองเปิดแอปอีกครั้ง หากยังพบปัญหาให้ตรวจสอบการติดตั้ง",
        buttons: ["ปิด"],
        noLink: true,
      });
    });
  }

  function returnToLauncherAfterStudioClose() {
    studioCloseBehavior = "launcher";
    if (studioWindow && !studioWindow.isDestroyed()) studioWindow.close();
  }

  function createStudioWindow() {
    if (studioWindow && !studioWindow.isDestroyed()) {
      launcherWindow?.hide();
      studioWindow.show();
      studioWindow.focus();
      return;
    }

    const studio = new BrowserWindow({
      title: "Video Studio — Solo Empire",
      width: 1440,
      height: 960,
      minWidth: 1024,
      minHeight: 680,
      show: false,
      ...createStudioWindowOptions(),
    });
    studioWindow = studio;
    launcherWindow?.hide();
    attachNavigationPolicy(studio, "studio");
    studio.once("ready-to-show", () => {
      if (!studio.isDestroyed()) studio.show();
    });
    studio.on("closed", () => {
      studioWindow = undefined;
      if (studioCloseBehavior === "launcher") {
        studioCloseBehavior = "quit";
        showLauncherWindow();
        return;
      }
      app.quit();
    });
    void studio.loadURL(WEB_ORIGIN).catch(() => {
      if (studio.isDestroyed()) return;
      returnToLauncherAfterStudioClose();
      showSafeDialog(launcherWindow, {
        type: "error",
        title: "เปิดหน้า Studio ไม่สำเร็จ",
        message: "ไม่สามารถเชื่อมต่อหน้า Video Studio ได้",
        detail: "ตรวจสอบสถานะบริการแล้วลองเปิดพื้นที่ทำงานอีกครั้ง",
        buttons: ["ปิด"],
        noLink: true,
      });
    });
  }

  function authorizeLauncherIpc(/** @type {import("electron").IpcMainInvokeEvent} */ event) {
    const launcherId = launcherWindow?.webContents.id;
    if (
      !launcherWindow ||
      event.sender !== launcherWindow.webContents ||
      !isTrustedLauncherSender(event.senderFrame, event.sender.mainFrame, event.sender.id, launcherId)
    ) {
      throw new Error("Unauthorized launcher request");
    }
  }

  ipcMain.handle(IPC_CHANNELS.getStatus, (/** @type {import("electron").IpcMainInvokeEvent} */ event) => {
    authorizeLauncherIpc(event);
    return currentStatus;
  });
  ipcMain.handle(IPC_CHANNELS.start, async (/** @type {import("electron").IpcMainInvokeEvent} */ event) => {
    authorizeLauncherIpc(event);
    return runManagerAction("start");
  });
  ipcMain.handle(IPC_CHANNELS.recover, async (/** @type {import("electron").IpcMainInvokeEvent} */ event) => {
    authorizeLauncherIpc(event);
    if (currentStatus.phase !== "needs-action" || currentStatus.action !== "recover-required") {
      return currentStatus;
    }
    return runManagerAction("recover");
  });

  function configureSessionSecurity() {
    const runtimeSession = session.defaultSession;
    runtimeSession.webRequest.onHeadersReceived((
      /** @type {import("electron").OnHeadersReceivedListenerDetails} */ details,
      /** @type {(response: import("electron").HeadersReceivedResponse) => void} */ callback,
    ) => {
      callback({
        responseHeaders: withStudioContentSecurityPolicy(
          details.url,
          details.responseHeaders ?? {},
        ),
      });
    });
    runtimeSession.setPermissionRequestHandler((
      /** @type {import("electron").WebContents} */ _webContents,
      /** @type {string} */ _permission,
      /** @type {(granted: boolean) => void} */ callback,
    ) => callback(false));
  }

  const guardedStopManager = { stop: () => runManagerAction("stop") };
  const menu = Menu.buildFromTemplate([
    {
      label: "Video Studio",
      submenu: [
        { label: "แสดงหน้าต่างเริ่มต้น", click: showLauncherWindow },
        {
          label: "เปิดพื้นที่ทำงาน",
          click: () => {
            if (currentStatus.phase === "ready") createStudioWindow();
            else showLauncherWindow();
          },
        },
        { label: "วิธีติดตั้ง Docker Desktop", click: openDockerInstallGuide },
        { type: "separator" },
        {
          label: "หยุด Engine",
          click: () => {
            void confirmStopEngine({
              showMessageBox: (dialogOptions, parentWindow) => parentWindow
                && !parentWindow.isDestroyed?.()
                ? dialog.showMessageBox(parentWindow, dialogOptions)
                : dialog.showMessageBox(dialogOptions),
              manager: guardedStopManager,
              parentWindow: studioWindow ?? launcherWindow,
            }).then((result) => {
              if (result === false) return;
              if (/** @type {RuntimeStatus} */ (result).phase !== "stopped") studioCloseBehavior = "quit";
            }).catch(() => {});
          },
        },
        { type: "separator" },
        { role: "quit", label: "ออกจากแอป" },
      ],
    },
  ]);
  Menu.setApplicationMenu(menu);

  app.on("activate", showLauncherWindow);
  app.whenReady().then(() => {
    configureSessionSecurity();
    protocol.handle(LAUNCHER_SCHEME, createLauncherProtocolHandler({
      isPackaged: app.isPackaged,
      appPath: app.getAppPath(),
      resourcesPath: process.resourcesPath ?? "",
    }));
    createLauncherWindow();
    void runManagerAction("start");
  }).catch(() => {
    publishStatus({
      phase: "failed",
      action: null,
      code: "compose-failed",
      message: "เริ่มต้น Video Studio ไม่สำเร็จ",
      services: currentStatus.services,
    });
    showLauncherWindow();
  });

  return true;
}

/** @param {{app: any, publishStatus: (status: RuntimeStatus) => void, platform: NodeJS.Platform}} options */
function createRuntimeManager({ app, publishStatus, platform }) {
  const paths = resolveRuntimePaths({
    isPackaged: app.isPackaged,
    appPath: app.getAppPath(),
    resourcesPath: process.resourcesPath ?? "",
    userDataPath: app.getPath("userData"),
  });
  return new RuntimeManager({
    dockerCli: new DockerCli({ platform }),
    paths,
    fetchImpl: globalThis.fetch,
    publishStatus,
  });
}

if (require.main === module) {
  const electron = require("electron");
  if (process.platform === "win32") electron.app.setAppUserModelId(APP_USER_MODEL_ID);
  // The package performs the Squirrel shortcut/uninstall action and quits when applicable.
  const squirrelStartupHandled = require("electron-squirrel-startup");
  startDesktopApp(electron, {
    squirrelStartupHandled,
    platform: process.platform,
    appUserModelIdAlreadySet: process.platform === "win32",
  });
}

module.exports = { APP_USER_MODEL_ID, createRuntimeManager, startDesktopApp };
