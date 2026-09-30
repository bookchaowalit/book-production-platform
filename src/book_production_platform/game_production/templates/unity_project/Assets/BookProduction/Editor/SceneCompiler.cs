using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using BookProduction.Runtime;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace BookProduction.Editor
{
    [Serializable]
    public sealed class UnityWorkerIR
    {
        public string schema_version;
        public string scene_id;
        public string template;
        public string palette;
        public WorldDTO world;
        public PlayerDTO player;
        public WallDTO[] walls;
        public ObjectiveDTO[] objectives;
        public ExitDTO exit;
        public AssetDTO[] assets;
        public AudioDTO audio;
    }

    [Serializable] public sealed class AssetDTO { public string asset_id; public string kind; public string filename; public string sha256; public string unity_path; }
    [Serializable] public sealed class AudioDTO { public string music; public string collect_sfx; }
    [Serializable] public sealed class CellDTO { public int x; public int y; }
    [Serializable] public sealed class WorldDTO { public int width; public int height; public float tile_size; }
    [Serializable] public sealed class PlayerDTO { public string id; public CellDTO spawn; public float speed; }
    [Serializable] public sealed class WallDTO { public string id; public CellDTO[] cells; }
    [Serializable] public sealed class ObjectiveDTO { public string id; public CellDTO cell; }
    [Serializable] public sealed class ExitDTO { public string id; public CellDTO cell; }

    [Serializable]
    public sealed class WorkerReport
    {
        public string status;
        public string unity_version;
        public string scene_path;
        public int player_count;
        public int wall_collider_count;
        public int objective_count;
        public int exit_count;
        public bool unique_entity_ids;
        public int external_resource_count;
        public int disallowed_component_count;
        public bool trusted_template_no_network_references;
        public int music_source_count;
        public bool collect_sound_configured;
    }

    public static class SceneCompiler
    {
        private const string WorkerInputPath = ".book-production/worker-input.json";
        private const string ArtRoot = "Assets/BookProduction/Art/";
        internal const string AudioRoot = "Assets/BookProduction/Audio/";
        internal const string MusicObjectName = "Music";
        private const string ComposerTemplatePath = "Assets/BookProduction/Source/scene-composer-template.unity";
        private const int WallSlotCount = 192;
        private const int ObjectiveSlotCount = 32;

        public static string GeneratedScenePath
        {
            get { return "Assets/Scenes/" + ReadWorkerInput().scene_id + ".unity"; }
        }

        public static void BuildFromGameIR()
        {
            UnityWorkerIR input = ReadWorkerInput();
            ValidateWorkerInput(input);

            PrepareSceneFolder();
            PrepareSpriteImport("player");
            PrepareSpriteImport("wall");
            PrepareSpriteImport("objective");
            PrepareSpriteImport("exit");
            AssetDatabase.Refresh();

            string scenePath = GeneratedScenePath;
            CopyComposerTemplate(scenePath);
            Scene scene = EditorSceneManager.OpenScene(scenePath, OpenSceneMode.Single);
            ApplyGameIRToScene(scene, input);
            ApplyAudio(scene, input);
            EditorSceneManager.SaveScene(scene, scenePath);
            WriteStableSceneMeta(scenePath, input.scene_id);
            AssetDatabase.Refresh();
            AssetDatabase.SaveAssets();

            EditorBuildSettings.scenes = EditorBuildSettings.scenes
                .Where(item => item.path != scenePath)
                .Concat(new[] { new EditorBuildSettingsScene(scenePath, true) })
                .ToArray();

            SceneVerificationReport verification = SceneVerifier.VerifyGeneratedScene();
            WriteWorkerReport(verification);
        }

        public static void BuildComposerTemplate()
        {
            PrepareSpriteImport("player");
            PrepareSpriteImport("wall");
            PrepareSpriteImport("objective");
            PrepareSpriteImport("exit");
            AssetDatabase.Refresh();

            var world = new WorldDTO { width = 8, height = 6, tile_size = 1f };
            Scene scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            var progressObject = new GameObject("GameProgress");
            GameProgress progress = progressObject.AddComponent<GameProgress>();
            progress.Initialize(0);
            // Audio slots (GameIR v2) live in the template so their file IDs are stable;
            // the compiler fills them or removes them, it never adds new objects.
            AudioSource collectSlot = progressObject.AddComponent<AudioSource>();
            collectSlot.playOnAwake = false;
            collectSlot.loop = false;
            collectSlot.spatialBlend = 0f;
            collectSlot.enabled = false;
            CreateCamera(world);
            AudioListener listenerSlot = UnityEngine.Object.FindAnyObjectByType<Camera>().gameObject.AddComponent<AudioListener>();
            listenerSlot.enabled = false;
            var musicSlot = new GameObject(MusicObjectName);
            AudioSource musicSource = musicSlot.AddComponent<AudioSource>();
            musicSource.playOnAwake = true;
            musicSource.loop = true;
            musicSource.spatialBlend = 0f;
            musicSlot.SetActive(false);
            CreateWorldBounds(world);

            var player = CreateEntity("PlayerSlot", "player-slot", "player-slot", new CellDTO(), world, "player");
            var playerBody = player.AddComponent<Rigidbody2D>();
            playerBody.gravityScale = 0f;
            playerBody.freezeRotation = true;
            playerBody.collisionDetectionMode = CollisionDetectionMode2D.Continuous;
            player.AddComponent<BoxCollider2D>().size = Vector2.one * 0.72f;
            player.AddComponent<TopDownPlayerController>().Initialize(5f, progress);
            player.SetActive(false);

            for (int index = 0; index < WallSlotCount; index++)
            {
                string slotId = "wall-slot-" + index.ToString("D3");
                var wall = CreateEntity("WallSlot-" + index.ToString("D3"), slotId, "wall-slot", new CellDTO(), world, "wall");
                wall.AddComponent<BoxCollider2D>().size = Vector2.one;
                wall.SetActive(false);
            }

            for (int index = 0; index < ObjectiveSlotCount; index++)
            {
                string slotId = "objective-slot-" + index.ToString("D3");
                var objective = CreateEntity("ObjectiveSlot-" + index.ToString("D3"), slotId, "objective-slot", new CellDTO(), world, "objective");
                CircleCollider2D collider = objective.AddComponent<CircleCollider2D>();
                collider.radius = 0.38f;
                collider.isTrigger = true;
                objective.AddComponent<Collectible>().Initialize(slotId, progress);
                objective.SetActive(false);
            }

            var exit = CreateEntity("ExitSlot", "exit-slot", "exit-slot", new CellDTO(), world, "exit");
            BoxCollider2D exitCollider = exit.AddComponent<BoxCollider2D>();
            exitCollider.size = Vector2.one * 0.8f;
            exitCollider.isTrigger = true;
            exit.AddComponent<ExitGate>().Initialize("exit-slot", progress);
            exit.SetActive(false);

            Directory.CreateDirectory(Path.GetDirectoryName(ComposerTemplatePath));
            EditorSceneManager.SaveScene(scene, ComposerTemplatePath);
            WriteStableSceneMeta(ComposerTemplatePath, "scene-composer-template");
            AssetDatabase.Refresh();
            AssetDatabase.SaveAssets();
        }

        internal static UnityWorkerIR ReadWorkerInput()
        {
            string absolutePath = Path.Combine(Directory.GetCurrentDirectory(), WorkerInputPath);
            if (!File.Exists(absolutePath))
            {
                throw new FileNotFoundException("worker_input_missing");
            }

            UnityWorkerIR input = JsonUtility.FromJson<UnityWorkerIR>(File.ReadAllText(absolutePath));
            if (input == null || string.IsNullOrWhiteSpace(input.scene_id))
            {
                throw new InvalidDataException("worker_input_invalid");
            }
            return input;
        }

        private static void ValidateWorkerInput(UnityWorkerIR input)
        {
            if ((input.schema_version != "game-ir.v1" && input.schema_version != "game-ir.v2")
                || input.template != "top-down-collect.v1"
                || input.palette != "palette-minimal.v1"
                || !Regex.IsMatch(input.scene_id, "^[a-z0-9]+(?:-[a-z0-9]+)*$")
                || input.scene_id.Length > 64
                || input.world == null
                || input.world.width < 3 || input.world.width > 64
                || input.world.height < 3 || input.world.height > 64
                || input.world.tile_size < 0.25f || input.world.tile_size > 4f
                || input.player == null
                || input.player.speed < 0.5f || input.player.speed > 12f
                || input.player.spawn == null
                || input.exit == null || input.exit.cell == null
                || input.walls == null || input.objectives == null
                || input.objectives.Length < 1 || input.objectives.Length > 32)
            {
                throw new InvalidDataException("worker_input_invalid");
            }

            int wallCellCount = 0;
            foreach (WallDTO wall in input.walls)
            {
                if (wall == null || wall.cells == null)
                {
                    throw new InvalidDataException("worker_input_invalid");
                }
                wallCellCount += wall.cells.Length;
            }
            if (wallCellCount > 192 || wallCellCount + input.objectives.Length + 2 > 256)
            {
                throw new InvalidDataException("worker_input_invalid");
            }
            if (input.objectives.Any(item => item == null || item.cell == null))
            {
                throw new InvalidDataException("worker_input_invalid");
            }
            ValidateAudioInput(input);
        }

        private static void ValidateAudioInput(UnityWorkerIR input)
        {
            AssetDTO[] assets = input.assets ?? new AssetDTO[0];
            string music = input.audio == null ? "" : input.audio.music ?? "";
            string collectSfx = input.audio == null ? "" : input.audio.collect_sfx ?? "";
            if (input.schema_version == "game-ir.v1")
            {
                if (assets.Length > 0 || music.Length > 0 || collectSfx.Length > 0)
                {
                    throw new InvalidDataException("worker_input_invalid");
                }
                return;
            }
            if (assets.Length > 4)
            {
                throw new InvalidDataException("worker_input_invalid");
            }
            foreach (AssetDTO asset in assets)
            {
                if (asset == null
                    || asset.kind != "audio"
                    || asset.asset_id == null
                    || !Regex.IsMatch(asset.asset_id, "^[a-z0-9]+(?:-[a-z0-9]+)*$")
                    || (asset.unity_path != AudioRoot + asset.asset_id + ".wav" && asset.unity_path != AudioRoot + asset.asset_id + ".ogg")
                    || !File.Exists(asset.unity_path))
                {
                    throw new InvalidDataException("worker_input_invalid");
                }
            }
            if (assets.Select(item => item.asset_id).Distinct(StringComparer.Ordinal).Count() != assets.Length
                || (music.Length > 0 && FindAsset(input, music) == null)
                || (collectSfx.Length > 0 && FindAsset(input, collectSfx) == null))
            {
                throw new InvalidDataException("worker_input_invalid");
            }
        }

        private static AssetDTO FindAsset(UnityWorkerIR input, string assetId)
        {
            return (input.assets ?? new AssetDTO[0]).FirstOrDefault(item => item != null && item.asset_id == assetId);
        }

        private static AudioClip LoadClip(UnityWorkerIR input, string assetId)
        {
            if (string.IsNullOrEmpty(assetId))
            {
                return null;
            }
            AssetDTO asset = FindAsset(input, assetId);
            AudioClip clip = asset == null ? null : AssetDatabase.LoadAssetAtPath<AudioClip>(asset.unity_path);
            if (clip == null)
            {
                throw new InvalidDataException("audio_asset_invalid");
            }
            return clip;
        }

        /// <summary>GameIR v2: looping music and a collect sound, both from staged local assets.</summary>
        private static void ApplyAudio(Scene scene, UnityWorkerIR input)
        {
            AudioClip music = LoadClip(input, input.audio == null ? null : input.audio.music);
            AudioClip collectSfx = LoadClip(input, input.audio == null ? null : input.audio.collect_sfx);

            GameObject[] roots = scene.GetRootGameObjects();
            GameObject progressObject = roots.FirstOrDefault(item => item.name == "GameProgress");
            GameObject musicObject = roots.FirstOrDefault(item => item.name == MusicObjectName);
            AudioListener listener = roots
                .SelectMany(root => root.GetComponentsInChildren<AudioListener>(true))
                .SingleOrDefault();
            AudioSource collectSource = progressObject == null ? null : progressObject.GetComponent<AudioSource>();
            AudioSource musicSource = musicObject == null ? null : musicObject.GetComponent<AudioSource>();
            if (listener == null || collectSource == null || musicSource == null)
            {
                throw new InvalidDataException("composer_audio_slot_missing");
            }

            if (music != null)
            {
                musicSource.clip = music;
                musicSource.loop = true;
                musicSource.playOnAwake = true;
                musicObject.SetActive(true);
            }
            else
            {
                UnityEngine.Object.DestroyImmediate(musicObject);
            }

            if (collectSfx != null)
            {
                collectSource.clip = collectSfx;
                collectSource.enabled = true;
                progressObject.GetComponent<GameProgress>().ConfigureCollectSound(collectSource);
            }
            else
            {
                UnityEngine.Object.DestroyImmediate(collectSource);
            }

            if (music != null || collectSfx != null)
            {
                listener.enabled = true;
            }
            else
            {
                UnityEngine.Object.DestroyImmediate(listener);
            }
        }

        private static void PrepareSceneFolder()
        {
            Directory.CreateDirectory("Assets/Scenes");
            const string metaPath = "Assets/Scenes.meta";
            if (!File.Exists(metaPath))
            {
                File.WriteAllText(metaPath, "fileFormatVersion: 2\nguid: 51b8fdc1d4e35a8b6f2c2190ea304c75\nfolderAsset: yes\nDefaultImporter:\n  externalObjects: {}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n");
            }
            AssetDatabase.Refresh();
        }

        private static void PrepareSpriteImport(string name)
        {
            string path = ArtRoot + name + ".png";
            var importer = AssetImporter.GetAtPath(path) as TextureImporter;
            if (importer == null)
            {
                throw new FileNotFoundException("palette_asset_missing");
            }

            if (importer.textureType != TextureImporterType.Sprite
                || importer.mipmapEnabled
                || importer.filterMode != FilterMode.Point
                || Math.Abs(importer.spritePixelsPerUnit - 16f) > 0.001f)
            {
                importer.textureType = TextureImporterType.Sprite;
                importer.mipmapEnabled = false;
                importer.filterMode = FilterMode.Point;
                importer.spritePixelsPerUnit = 16f;
                importer.SaveAndReimport();
            }
        }

        private static Sprite LoadSprite(string name)
        {
            Sprite sprite = AssetDatabase.LoadAssetAtPath<Sprite>(ArtRoot + name + ".png");
            if (sprite == null)
            {
                throw new InvalidDataException("palette_asset_invalid");
            }
            return sprite;
        }

        private static Vector3 CellCenter(CellDTO cell, WorldDTO world)
        {
            float halfWidth = (world.width - 1) * world.tile_size * 0.5f;
            float halfHeight = (world.height - 1) * world.tile_size * 0.5f;
            return new Vector3(cell.x * world.tile_size - halfWidth, cell.y * world.tile_size - halfHeight, 0f);
        }

        private static void CreateCamera(WorldDTO world)
        {
            var cameraObject = new GameObject("Main Camera");
            cameraObject.tag = "MainCamera";
            cameraObject.transform.position = new Vector3(0f, 0f, -10f);
            Camera camera = cameraObject.AddComponent<Camera>();
            camera.orthographic = true;
            camera.orthographicSize = Math.Max(world.height, world.width) * world.tile_size * 0.6f;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = new Color(0.055f, 0.075f, 0.11f, 1f);
        }

        private static void CreateWorldBounds(WorldDTO world)
        {
            float width = world.width * world.tile_size;
            float height = world.height * world.tile_size;
            float thickness = world.tile_size;
            float halfWidth = width * 0.5f;
            float halfHeight = height * 0.5f;
            CreateBoundary("Boundary-West", new Vector2(-halfWidth - thickness * 0.5f, 0f), new Vector2(thickness, height + thickness * 2f));
            CreateBoundary("Boundary-East", new Vector2(halfWidth + thickness * 0.5f, 0f), new Vector2(thickness, height + thickness * 2f));
            CreateBoundary("Boundary-South", new Vector2(0f, -halfHeight - thickness * 0.5f), new Vector2(width, thickness));
            CreateBoundary("Boundary-North", new Vector2(0f, halfHeight + thickness * 0.5f), new Vector2(width, thickness));
        }

        private static void CreateBoundary(string name, Vector2 position, Vector2 size)
        {
            var boundary = new GameObject(name);
            boundary.transform.position = position;
            boundary.AddComponent<BoxCollider2D>().size = size;
        }

        private static void CopyComposerTemplate(string scenePath)
        {
            if (!File.Exists(ComposerTemplatePath))
            {
                throw new FileNotFoundException("composer_template_missing");
            }
            if (EditorSceneManager.GetActiveScene().path == scenePath)
            {
                EditorSceneManager.OpenScene(ComposerTemplatePath, OpenSceneMode.Single);
            }
            if (File.Exists(scenePath) && !AssetDatabase.DeleteAsset(scenePath))
            {
                throw new IOException("generated_scene_could_not_be_replaced");
            }

            Directory.CreateDirectory(Path.GetDirectoryName(scenePath));
            File.Copy(ComposerTemplatePath, scenePath, true);
            WriteStableSceneMeta(scenePath, ReadWorkerInput().scene_id);
            AssetDatabase.Refresh();
        }

        private static void ApplyGameIRToScene(Scene scene, UnityWorkerIR input)
        {
            GameObject progressObject = scene.GetRootGameObjects().FirstOrDefault(item => item.name == "GameProgress");
            GameProgress progress = progressObject == null ? null : progressObject.GetComponent<GameProgress>();
            if (progress == null)
            {
                throw new InvalidDataException("composer_progress_missing");
            }
            progress.Initialize(input.objectives.Length);
            UpdateCamera(scene, input.world);
            UpdateWorldBounds(scene, input.world);

            SceneEntityIdentity player = FindSlots(scene, "player-slot").SingleOrDefault();
            if (player == null)
            {
                throw new InvalidDataException("composer_player_slot_missing");
            }
            ConfigureEntity(player, "Player-" + input.player.id, input.player.id, "player", input.player.spawn, input.world, "player", 1);
            player.GetComponent<BoxCollider2D>().size = Vector2.one * 0.72f;
            player.GetComponent<TopDownPlayerController>().Initialize(input.player.speed, progress);
            player.gameObject.SetActive(true);

            SceneEntityIdentity[] wallSlots = FindSlots(scene, "wall-slot");
            int wallIndex = 0;
            foreach (WallDTO wall in input.walls.OrderBy(item => item.id, StringComparer.Ordinal))
            {
                foreach (CellDTO cell in wall.cells.OrderBy(item => item.y).ThenBy(item => item.x))
                {
                    if (wallIndex >= wallSlots.Length)
                    {
                        throw new InvalidDataException("composer_wall_slots_exceeded");
                    }
                    SceneEntityIdentity slot = wallSlots[wallIndex++];
                    ConfigureEntity(
                        slot,
                        "Wall-" + wall.id + "-" + cell.x + "-" + cell.y,
                        "wall:" + wall.id + ":" + cell.x + ":" + cell.y,
                        "wall",
                        cell,
                        input.world,
                        "wall",
                        1
                    );
                    slot.GetComponent<BoxCollider2D>().size = Vector2.one;
                    slot.gameObject.SetActive(true);
                }
            }
            DestroyUnusedSlots(wallSlots, wallIndex);

            SceneEntityIdentity[] objectiveSlots = FindSlots(scene, "objective-slot");
            ObjectiveDTO[] objectives = input.objectives.OrderBy(item => item.id, StringComparer.Ordinal).ToArray();
            if (objectives.Length > objectiveSlots.Length)
            {
                throw new InvalidDataException("composer_objective_slots_exceeded");
            }
            for (int index = 0; index < objectives.Length; index++)
            {
                ObjectiveDTO objective = objectives[index];
                SceneEntityIdentity slot = objectiveSlots[index];
                ConfigureEntity(slot, "Objective-" + objective.id, objective.id, "objective", objective.cell, input.world, "objective", 2);
                CircleCollider2D collider = slot.GetComponent<CircleCollider2D>();
                collider.radius = 0.38f;
                collider.isTrigger = true;
                slot.GetComponent<Collectible>().Initialize(objective.id, progress);
                slot.gameObject.SetActive(true);
            }
            DestroyUnusedSlots(objectiveSlots, objectives.Length);

            SceneEntityIdentity exit = FindSlots(scene, "exit-slot").SingleOrDefault();
            if (exit == null)
            {
                throw new InvalidDataException("composer_exit_slot_missing");
            }
            ConfigureEntity(exit, "Exit-" + input.exit.id, input.exit.id, "exit", input.exit.cell, input.world, "exit", 2);
            BoxCollider2D exitCollider = exit.GetComponent<BoxCollider2D>();
            exitCollider.size = Vector2.one * 0.8f;
            exitCollider.isTrigger = true;
            exit.GetComponent<ExitGate>().Initialize(input.exit.id, progress);
            exit.gameObject.SetActive(true);
        }

        private static SceneEntityIdentity[] FindSlots(Scene scene, string entityType)
        {
            return scene.GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<SceneEntityIdentity>(true))
                .Where(identity => identity.EntityType == entityType)
                .OrderBy(identity => identity.EntityId, StringComparer.Ordinal)
                .ToArray();
        }

        private static void DestroyUnusedSlots(SceneEntityIdentity[] slots, int usedCount)
        {
            for (int index = usedCount; index < slots.Length; index++)
            {
                UnityEngine.Object.DestroyImmediate(slots[index].gameObject);
            }
        }

        private static void ConfigureEntity(
            SceneEntityIdentity identity,
            string name,
            string entityId,
            string entityType,
            CellDTO cell,
            WorldDTO world,
            string spriteName,
            int sortingOrder
        )
        {
            GameObject gameObject = identity.gameObject;
            identity.Initialize(entityId, entityType);
            gameObject.name = name;
            gameObject.transform.position = CellCenter(cell, world);
            gameObject.transform.localScale = Vector3.one * world.tile_size;
            SpriteRenderer renderer = gameObject.GetComponent<SpriteRenderer>();
            renderer.sprite = LoadSprite(spriteName);
            renderer.sortingOrder = sortingOrder;
        }

        private static void UpdateCamera(Scene scene, WorldDTO world)
        {
            Camera camera = scene.GetRootGameObjects()
                .SelectMany(root => root.GetComponentsInChildren<Camera>(true))
                .SingleOrDefault();
            if (camera == null)
            {
                throw new InvalidDataException("composer_camera_missing");
            }
            camera.transform.position = new Vector3(0f, 0f, -10f);
            camera.orthographic = true;
            camera.orthographicSize = Math.Max(world.height, world.width) * world.tile_size * 0.6f;
        }

        private static void UpdateWorldBounds(Scene scene, WorldDTO world)
        {
            float width = world.width * world.tile_size;
            float height = world.height * world.tile_size;
            float thickness = world.tile_size;
            float halfWidth = width * 0.5f;
            float halfHeight = height * 0.5f;
            UpdateBoundary(scene, "Boundary-West", new Vector2(-halfWidth - thickness * 0.5f, 0f), new Vector2(thickness, height + thickness * 2f));
            UpdateBoundary(scene, "Boundary-East", new Vector2(halfWidth + thickness * 0.5f, 0f), new Vector2(thickness, height + thickness * 2f));
            UpdateBoundary(scene, "Boundary-South", new Vector2(0f, -halfHeight - thickness * 0.5f), new Vector2(width, thickness));
            UpdateBoundary(scene, "Boundary-North", new Vector2(0f, halfHeight + thickness * 0.5f), new Vector2(width, thickness));
        }

        private static void UpdateBoundary(Scene scene, string name, Vector2 position, Vector2 size)
        {
            GameObject boundary = scene.GetRootGameObjects().SingleOrDefault(item => item.name == name);
            if (boundary == null)
            {
                throw new InvalidDataException("composer_boundary_missing");
            }
            boundary.transform.position = position;
            boundary.GetComponent<BoxCollider2D>().size = size;
        }

        private static GameObject CreateEntity(
            string name,
            string entityId,
            string entityType,
            CellDTO cell,
            WorldDTO world,
            string spriteName
        )
        {
            var gameObject = new GameObject(name);
            gameObject.transform.position = CellCenter(cell, world);
            gameObject.transform.localScale = Vector3.one * world.tile_size;
            gameObject.AddComponent<SceneEntityIdentity>().Initialize(entityId, entityType);
            SpriteRenderer renderer = gameObject.AddComponent<SpriteRenderer>();
            renderer.sprite = LoadSprite(spriteName);
            renderer.sortingOrder = entityType == "objective" || entityType == "exit" ? 2 : 1;
            return gameObject;
        }

        private static void WriteStableSceneMeta(string scenePath, string sceneId)
        {
            string metaPath = scenePath + ".meta";
            byte[] digest;
            using (System.Security.Cryptography.SHA256 hash = System.Security.Cryptography.SHA256.Create())
            {
                digest = hash.ComputeHash(System.Text.Encoding.UTF8.GetBytes("book-production-game-scene:" + sceneId));
            }
            string guid = BitConverter.ToString(digest).Replace("-", string.Empty).Substring(0, 32).ToLowerInvariant();
            File.WriteAllText(metaPath, "fileFormatVersion: 2\nguid: " + guid + "\nDefaultImporter:\n  externalObjects: {}\n  userData: \n  assetBundleName: \n  assetBundleVariant: \n");
        }

        private static void WriteWorkerReport(SceneVerificationReport verification)
        {
            string folder = Path.Combine(Directory.GetCurrentDirectory(), ".book-production");
            Directory.CreateDirectory(folder);
            var report = new WorkerReport
            {
                status = verification.IsValid ? "verified" : "rejected",
                unity_version = Application.unityVersion,
                scene_path = GeneratedScenePath,
                player_count = verification.PlayerCount,
                wall_collider_count = verification.WallColliderCount,
                objective_count = verification.ObjectiveCount,
                exit_count = verification.ExitCount,
                unique_entity_ids = verification.UniqueEntityIds,
                external_resource_count = verification.ExternalResourceCount,
                disallowed_component_count = verification.DisallowedComponentCount,
                trusted_template_no_network_references = verification.TrustedTemplateNoNetworkReferences,
                music_source_count = verification.MusicSourceCount,
                collect_sound_configured = verification.CollectSoundConfigured
            };
            File.WriteAllText(Path.Combine(folder, "worker-report.json"), JsonUtility.ToJson(report, true));
            if (!verification.IsValid)
            {
                throw new InvalidDataException("generated_scene_verification_failed");
            }
        }
    }
}
