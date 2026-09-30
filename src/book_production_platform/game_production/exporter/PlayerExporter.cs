using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEngine;

namespace BookProduction.Editor
{
    [Serializable]
    public sealed class PlayerExportInput
    {
        public string target;
        public string output_dir;
        public string product_name;
    }

    [Serializable]
    public sealed class PlayerExportReport
    {
        public string status = "failed";
        public string unity_version;
        public string target;
        public string result;
        public int total_errors;
        public int total_warnings;
        public string[] scenes = new string[0];
        public string message = "";
    }

    // Injected by `book-production game export` into a disposable copy of an
    // accepted project; never part of the published editable project.
    public static class PlayerExporter
    {
        public static void Build()
        {
            var root = Path.GetFullPath(Path.Combine(Application.dataPath, "..", ".book-production"));
            var report = new PlayerExportReport { unity_version = Application.unityVersion };
            var exitCode = 1;
            try
            {
                var input = JsonUtility.FromJson<PlayerExportInput>(
                    File.ReadAllText(Path.Combine(root, "export-input.json")));
                report.target = input.target;
                var scenes = EditorBuildSettings.scenes.Where(s => s.enabled).Select(s => s.path).ToArray();
                if (scenes.Length != 1)
                {
                    throw new InvalidOperationException("Exactly one enabled build scene is required");
                }
                report.scenes = scenes;

                BuildTarget target;
                BuildTargetGroup group;
                string location;
                switch (input.target)
                {
                    case "web":
                        target = BuildTarget.WebGL;
                        group = BuildTargetGroup.WebGL;
                        location = input.output_dir;
                        // Gzip with a JS fallback loads on any static host, including
                        // servers that do not send Content-Encoding headers.
                        PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Gzip;
                        PlayerSettings.WebGL.decompressionFallback = true;
                        break;
                    case "windows":
                        target = BuildTarget.StandaloneWindows64;
                        group = BuildTargetGroup.Standalone;
                        location = Path.Combine(input.output_dir, input.product_name + ".exe");
                        break;
                    default:
                        throw new InvalidOperationException("Unsupported export target");
                }

                if (!BuildPipeline.IsBuildTargetSupported(group, target))
                {
                    report.status = "target_unsupported";
                    throw new InvalidOperationException("Unity build support module is not installed");
                }

                PlayerSettings.productName = input.product_name;
                PlayerSettings.companyName = "Book Production";
                var build = BuildPipeline.BuildPlayer(new BuildPlayerOptions
                {
                    scenes = scenes,
                    target = target,
                    targetGroup = group,
                    locationPathName = location,
                    options = BuildOptions.None,
                });
                report.result = build.summary.result.ToString();
                report.total_errors = build.summary.totalErrors;
                report.total_warnings = build.summary.totalWarnings;
                if (build.summary.result == BuildResult.Succeeded)
                {
                    report.status = "built";
                    exitCode = 0;
                }
            }
            catch (Exception error)
            {
                report.message = error.GetType().Name;
            }

            File.WriteAllText(Path.Combine(root, "export-report.json"), JsonUtility.ToJson(report, true));
            EditorApplication.Exit(exitCode);
        }
    }
}
