/**
 * @typedef {{
 *   isDestroyed?: () => boolean,
 *   isMinimized?: () => boolean,
 *   restore?: () => void,
 *   show?: () => void,
 *   focus?: () => void
 * }} LauncherWindow
 * @typedef {{
 *   requestSingleInstanceLock: () => boolean,
 *   quit: () => void,
 *   on: (event: string, listener: (...args: unknown[]) => void) => unknown
 * }} ElectronAppLike
 * @typedef {{
 *   type: "warning",
 *   title: string,
 *   message: string,
 *   detail: string,
 *   buttons: string[],
 *   defaultId: number,
 *   cancelId: number,
 *   noLink: true
 * }} StopDialogOptions
 */

const SQUIRREL_STARTUP_ARGUMENTS = new Set([
  "--squirrel-install",
  "--squirrel-updated",
  "--squirrel-uninstall",
  "--squirrel-obsolete",
]);

/**
 * Acquire Electron's single-instance lock and restore/focus the existing launcher when another
 * copy starts. Main integration must call this before `app.whenReady()` or creating any window.
 * A false return means the caller must exit without creating windows.
 *
 * @param {{ app: ElectronAppLike, getLauncherWindow: () => LauncherWindow | undefined }} options
 * @returns {boolean} Whether this process owns the single-instance lock.
 */
function acquireSingleInstanceLock({ app, getLauncherWindow }) {
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return false;
  }

  app.on("second-instance", () => {
    const window = getLauncherWindow();
    if (!window || window.isDestroyed?.()) return;
    if (window.isMinimized?.()) window.restore?.();
    window.show?.();
    window.focus?.();
  });
  return true;
}

/**
 * Detects Squirrel.Windows startup events handled by electron-squirrel-startup.
 * Main integration should quit and return before acquiring the lock or creating windows when true.
 *
 * @param {string[]} argv Usually `process.argv`.
 * @returns {boolean}
 */
function isSquirrelStartup(argv) {
  const startupArgument = argv[1];
  return startupArgument !== undefined && SQUIRREL_STARTUP_ARGUMENTS.has(startupArgument);
}

/**
 * Show the warning dialog and stop the runtime only when the explicit first button is confirmed.
 * `showMessageBox` has the injected signature `(options, parentWindow) => Promise<{ response: number }>`,
 * allowing main to adapt Electron's dialog overload without coupling this helper to Electron.
 *
 * @param {{
 *   showMessageBox: (options: StopDialogOptions, parentWindow?: LauncherWindow) => Promise<{ response: number }>,
 *   manager: { stop: () => unknown | Promise<unknown> },
 *   parentWindow?: LauncherWindow
 * }} options
 * @returns {Promise<unknown | false>} The manager result after confirmation, otherwise false.
 */
async function confirmStopEngine({ showMessageBox, manager, parentWindow }) {
  /** @type {StopDialogOptions} */
  const dialogOptions = {
    type: "warning",
    title: "หยุด Engine",
    message: "การหยุด Engine อาจขัดจังหวะงานที่กำลังประมวลผล",
    detail: "ยืนยันได้จากปุ่มหยุดเท่านั้น งานที่บันทึกแล้วจะยังคงอยู่",
    buttons: ["หยุด Engine", "ยกเลิก"],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
  };
  const result = await showMessageBox(dialogOptions, parentWindow);
  if (result.response !== 0) return false;
  return manager.stop();
}

module.exports = { acquireSingleInstanceLock, isSquirrelStartup, confirmStopEngine };
