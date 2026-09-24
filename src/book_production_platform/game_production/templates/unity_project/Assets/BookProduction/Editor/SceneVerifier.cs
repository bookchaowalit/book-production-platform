using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BookProduction.Runtime;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace BookProduction.Editor
{
    [Serializable]
    public sealed class SceneVerificationReport
    {
        public int PlayerCount;
        public int WallColliderCount;
        public int ObjectiveCount;
        public int ExitCount;
        public bool UniqueEntityIds;
        public int ExternalResourceCount;
        public int DisallowedComponentCount;
        public bool TrustedTemplateNoNetworkReferences;

        public bool IsValid
        {
            get
            {
                return PlayerCount == 1
                    && WallColliderCount > 0
                    && ObjectiveCount > 0
                    && ExitCount == 1
                    && UniqueEntityIds
                    && ExternalResourceCount == 0
                    && DisallowedComponentCount == 0
                    && TrustedTemplateNoNetworkReferences;
            }
        }
    }

    public static class SceneVerifier
    {
        public static SceneVerificationReport VerifyGeneratedScene()
        {
            var scene = EditorSceneManager.OpenScene(SceneCompiler.GeneratedScenePath, OpenSceneMode.Single);
            GameObject[] roots = scene.GetRootGameObjects();
            SceneEntityIdentity[] identities = roots
                .SelectMany(root => root.GetComponentsInChildren<SceneEntityIdentity>(true))
                .ToArray();
            SpriteRenderer[] renderers = roots
                .SelectMany(root => root.GetComponentsInChildren<SpriteRenderer>(true))
                .ToArray();
            Component[] components = roots
                .SelectMany(root => root.GetComponentsInChildren<Component>(true))
                .ToArray();
            string[] entityIds = identities.Select(identity => identity.EntityId).ToArray();

            var report = new SceneVerificationReport
            {
                PlayerCount = identities.Count(identity => identity.EntityType == "player"),
                WallColliderCount = identities.Count(identity =>
                    identity.EntityType == "wall" && identity.GetComponent<BoxCollider2D>() != null),
                ObjectiveCount = identities.Count(identity =>
                    identity.EntityType == "objective" && identity.GetComponent<Collectible>() != null),
                ExitCount = identities.Count(identity =>
                    identity.EntityType == "exit" && identity.GetComponent<ExitGate>() != null),
                UniqueEntityIds = entityIds.All(id => !string.IsNullOrWhiteSpace(id))
                    && entityIds.Distinct(StringComparer.Ordinal).Count() == entityIds.Length,
                DisallowedComponentCount = CountDisallowedComponents(components),
                TrustedTemplateNoNetworkReferences = TrustedSourceHasNoNetworkReferences(),
                ExternalResourceCount = renderers.Count(renderer =>
                {
                    if (renderer.sprite == null) return true;
                    string path = AssetDatabase.GetAssetPath(renderer.sprite);
                    return !path.StartsWith("Assets/BookProduction/Art/", StringComparison.Ordinal);
                })
            };
            if (!report.IsValid)
            {
                throw new InvalidDataException("generated_scene_verification_failed");
            }
            return report;
        }

        private static int CountDisallowedComponents(IEnumerable<Component> components)
        {
            var allowed = new HashSet<Type>
            {
                typeof(Transform),
                typeof(Camera),
                typeof(GameProgress),
                typeof(SceneEntityIdentity),
                typeof(SpriteRenderer),
                typeof(Rigidbody2D),
                typeof(BoxCollider2D),
                typeof(CircleCollider2D),
                typeof(TopDownPlayerController),
                typeof(Collectible),
                typeof(ExitGate)
            };
            return components.Count(component => component == null || !allowed.Contains(component.GetType()));
        }

        private static bool TrustedSourceHasNoNetworkReferences()
        {
            const string sourceRoot = "Assets/BookProduction";
            if (!Directory.Exists(sourceRoot))
            {
                return false;
            }

            string[] files = Directory.GetFiles(sourceRoot, "*.cs", SearchOption.AllDirectories);
            if (files.Length == 0)
            {
                return false;
            }

            string[] blockedTokens =
            {
                "UnityWeb" + "Request",
                "System.Net" + ".Http",
                "System.Net" + ".Sockets",
                "http:" + "//",
                "https:" + "//"
            };
            return files.All(path =>
            {
                string source = File.ReadAllText(path);
                return blockedTokens.All(token => source.IndexOf(token, StringComparison.OrdinalIgnoreCase) < 0);
            });
        }
    }
}
