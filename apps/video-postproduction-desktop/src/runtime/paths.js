const path = require("node:path");

/**
 * @param {{ isPackaged: boolean, appPath: string, resourcesPath: string, userDataPath: string }} input
 * @returns {{ bundleRoot: string, composeFile: string, composeProjectDirectory: string, userDataPath: string }}
 */
exports.resolveRuntimePaths = function resolveRuntimePaths(input) {
  const bundleRoot = input.isPackaged
    ? path.join(input.resourcesPath, "video-runtime")
    // npm start uses tracked source directly; staging runs only for packaging.
    : path.resolve(input.appPath, "..", "..");
  const composeFile = path.join(bundleRoot, "services", "video-postproduction", "docker-compose.yml");
  return {
    bundleRoot,
    composeFile,
    composeProjectDirectory: path.dirname(composeFile),
    userDataPath: input.userDataPath,
  };
};
