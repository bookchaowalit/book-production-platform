/** @typedef {{nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true, preload: string}} LauncherWebPreferences */
/** @typedef {{nodeIntegration: false, contextIsolation: true, sandbox: true, webSecurity: true}} StudioWebPreferences */

/**
 * @param {string} preloadPath
 * @returns {{webPreferences: LauncherWebPreferences}}
 */
function createLauncherWindowOptions(preloadPath) {
  if (typeof preloadPath !== "string" || preloadPath.trim() === "") {
    throw new TypeError("A launcher preload path is required.");
  }

  return {
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      preload: preloadPath
    }
  };
}

/** @returns {{webPreferences: StudioWebPreferences}} */
function createStudioWindowOptions() {
  return {
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true
    }
  };
}

module.exports = {
  createLauncherWindowOptions,
  createStudioWindowOptions
};
