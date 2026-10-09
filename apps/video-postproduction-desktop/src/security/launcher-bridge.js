/** @typedef {import("../runtime/types").RuntimeStatus} RuntimeStatus */

/**
 * Stable IPC names shared by the Electron main process and the isolated launcher preload.
 * Main-process handlers should authorize the launcher webContents and frame before handling them.
 */
const IPC_CHANNELS = Object.freeze({
  getStatus: "video-studio:get-status",
  start: "video-studio:start",
  recover: "video-studio:recover",
  status: "video-studio:status",
});

/**
 * @typedef {(event: unknown, status: RuntimeStatus) => void} IpcStatusListener
 * @typedef {{
 *   invoke: (channel: string) => Promise<RuntimeStatus>,
 *   on: (channel: string, listener: IpcStatusListener) => unknown,
 *   removeListener: (channel: string, listener: IpcStatusListener) => unknown
 * }} LauncherIpcRenderer
 */

/**
 * Creates the launcher's deliberately narrow renderer API. Main-process IPC handlers must use
 * the same exported IPC_CHANNELS and independently authorize the sender before invoking actions.
 *
 * @param {LauncherIpcRenderer} ipcRenderer Electron's ipcRenderer, injected for isolated tests.
 * @returns {{
 *   getStatus: () => Promise<RuntimeStatus>,
 *   start: () => Promise<RuntimeStatus>,
 *   recover: () => Promise<RuntimeStatus>,
 *   onStatus: (listener: (status: RuntimeStatus) => void) => () => void
 * }}
 */
function createLauncherBridge(ipcRenderer) {
  return {
    getStatus: () => ipcRenderer.invoke(IPC_CHANNELS.getStatus),
    start: () => ipcRenderer.invoke(IPC_CHANNELS.start),
    recover: () => ipcRenderer.invoke(IPC_CHANNELS.recover),
    onStatus(listener) {
      /** @type {IpcStatusListener} */
      const pairedListener = (_event, status) => listener(status);
      ipcRenderer.on(IPC_CHANNELS.status, pairedListener);
      let subscribed = true;
      return () => {
        if (!subscribed) return;
        subscribed = false;
        ipcRenderer.removeListener(IPC_CHANNELS.status, pairedListener);
      };
    },
  };
}

module.exports = { IPC_CHANNELS, createLauncherBridge };
