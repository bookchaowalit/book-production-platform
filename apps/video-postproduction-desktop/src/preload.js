const { contextBridge, ipcRenderer } = require("electron");

// Sandboxed Electron preloads cannot require local CommonJS files.
// Keep these channels aligned with security/launcher-bridge.js; the preload test checks parity.
const IPC_CHANNELS = Object.freeze({
  getStatus: "video-studio:get-status",
  start: "video-studio:start",
  recover: "video-studio:recover",
  status: "video-studio:status",
});

contextBridge.exposeInMainWorld("videoStudio", {
  getStatus: () => ipcRenderer.invoke(IPC_CHANNELS.getStatus),
  start: () => ipcRenderer.invoke(IPC_CHANNELS.start),
  recover: () => ipcRenderer.invoke(IPC_CHANNELS.recover),
  /** @param {(status: import("./runtime/types").RuntimeStatus) => void} listener */
  onStatus(listener) {
    /** @param {unknown} _event @param {import("./runtime/types").RuntimeStatus} status */
    const pairedListener = (_event, status) => listener(status);
    ipcRenderer.on(IPC_CHANNELS.status, pairedListener);
    let subscribed = true;
    return () => {
      if (!subscribed) return;
      subscribed = false;
      ipcRenderer.removeListener(IPC_CHANNELS.status, pairedListener);
    };
  },
});
