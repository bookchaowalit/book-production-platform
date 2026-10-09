const path = require("node:path");

module.exports = {
  packagerConfig: {
    asar: true,
    executableName: "SoloEmpireVideoStudio",
    extraResource: ["dist/video-runtime", "src/launcher"],
  },
  outDir: path.resolve(__dirname, "dist", "forge-out"),
  makers: [
    {
      name: "@electron-forge/maker-squirrel",
      config: {
        name: "SoloEmpireVideoStudio",
        title: "Solo Empire Video Studio",
        authors: "Solo Empire",
        description: "Windows desktop shell for the local-first video post-production studio.",
        setupExe: "Solo Empire Video Studio Setup.exe",
      },
    },
  ],
};
