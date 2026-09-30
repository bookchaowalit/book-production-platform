using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using BookProduction.Runtime;
using NUnit.Framework;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.TestTools;

namespace BookProduction.Tests.PlayMode
{
    public sealed class GameplayTraceTests
    {
        [UnityTest]
        public IEnumerator ExitWinsOnlyAfterEveryObjectiveIsCollected()
        {
            TestGameInput input = SceneTestDriver.ReadGameInput();
            SceneTestDriver.BeginLoadGeneratedScene(input.scene_id);
            yield return null;

            GameProgress progress = SceneTestDriver.FindGeneratedProgress();
            Assert.That(progress, Is.Not.Null);
            yield return SceneTestDriver.PlayCollectionTrace(progress, input);

            Assert.That(progress.CollectedCount, Is.EqualTo(progress.TotalObjectives));
            Assert.That(progress.IsWon, Is.True);
            Assert.That(progress.Events[0], Is.EqualTo("exit_blocked:" + input.exit.id));
            Assert.That(progress.Events, Does.Contain("player_moved"));
            Assert.That(
                progress.Events.Where(item => item.StartsWith("objective_collected:", StringComparison.Ordinal)),
                Is.EquivalentTo(input.objectives.Select(item => "objective_collected:" + item.id))
            );
            int lastCollection = progress.Events.FindLastIndex(item =>
                item.StartsWith("objective_collected:", StringComparison.Ordinal));
            int winIndex = progress.Events.LastIndexOf("game_won:" + input.exit.id);
            Assert.That(lastCollection, Is.GreaterThanOrEqualTo(0));
            Assert.That(winIndex, Is.GreaterThan(lastCollection));
            Assert.That(progress.Events[progress.Events.Count - 1], Is.EqualTo("game_won:" + input.exit.id));
            Assert.That(
                progress.CollectSoundCount,
                Is.EqualTo(progress.HasCollectSound ? progress.TotalObjectives : 0)
            );

            CollectionTraceWriter.Write(progress.Events);
            Assert.That(
                File.Exists(Path.Combine(Directory.GetCurrentDirectory(), ".book-production", "gameplay-trace.json")),
                Is.True
            );
        }
    }

    [Serializable]
    public sealed class TestGameInput
    {
        public string scene_id;
        public TestWorld world;
        public TestPlayer player;
        public TestWall[] walls;
        public TestObjective[] objectives;
        public TestExit exit;
    }

    [Serializable]
    public sealed class TestWorld
    {
        public int width;
        public int height;
        public float tile_size;
    }

    [Serializable]
    public sealed class TestCell
    {
        public int x;
        public int y;
    }

    [Serializable]
    public sealed class TestPlayer
    {
        public TestCell spawn;
        public float speed;
    }

    [Serializable]
    public sealed class TestWall
    {
        public string id;
        public TestCell[] cells;
    }

    [Serializable]
    public sealed class TestObjective
    {
        public string id;
        public TestCell cell;
    }

    [Serializable]
    public sealed class TestExit
    {
        public string id;
        public TestCell cell;
    }

    internal static class SceneTestDriver
    {
        private static readonly Vector2Int[] Directions =
        {
            Vector2Int.left,
            Vector2Int.right,
            Vector2Int.down,
            Vector2Int.up
        };

        public static TestGameInput ReadGameInput()
        {
            string inputPath = Path.Combine(Directory.GetCurrentDirectory(), ".book-production", "worker-input.json");
            if (!File.Exists(inputPath))
            {
                throw new FileNotFoundException("worker_input_missing");
            }

            TestGameInput input = JsonUtility.FromJson<TestGameInput>(File.ReadAllText(inputPath));
            if (input == null || input.world == null || input.player == null || input.player.spawn == null || input.exit == null
                || input.walls == null || input.objectives == null)
            {
                throw new InvalidDataException("worker_input_invalid");
            }
            return input;
        }

        public static void BeginLoadGeneratedScene(string sceneId)
        {
            SceneManager.LoadScene(sceneId, LoadSceneMode.Single);
        }

        public static GameProgress FindGeneratedProgress()
        {
            return UnityEngine.Object.FindAnyObjectByType<GameProgress>();
        }

        public static IEnumerator PlayCollectionTrace(GameProgress progress, TestGameInput input)
        {
            TopDownPlayerController controller = UnityEngine.Object.FindAnyObjectByType<TopDownPlayerController>();
            Assert.That(controller, Is.Not.Null);
            if (controller == null)
            {
                yield break;
            }

            var scriptedInput = new ScriptedInputSource();
            float testSpeed = input.world.tile_size / (Time.fixedDeltaTime * 4f);
            controller.Initialize(testSpeed, progress);
            controller.InputSource = scriptedInput;
            Rigidbody2D body = controller.GetComponent<Rigidbody2D>();

            body.position = CellCenter(input.exit.cell, input.world);
            Physics2D.SyncTransforms();
            yield return new WaitForFixedUpdate();
            Assert.That(progress.IsWon, Is.False);
            Assert.That(progress.Events, Does.Contain("exit_blocked:" + input.exit.id));

            Vector2Int startCell = ToGridCell(input.player.spawn);
            body.position = CellCenter(input.player.spawn, input.world);
            Physics2D.SyncTransforms();
            Vector2Int movementCell = FindOpenNeighbor(startCell, input);
            Assert.That(movementCell, Is.Not.EqualTo(startCell));
            Vector2 movementDirection = movementCell - startCell;
            scriptedInput.Direction = movementDirection;
            yield return new WaitForFixedUpdate();
            scriptedInput.Direction = Vector2.zero;
            Assert.That(Vector2.Distance(body.position, CellCenter(input.player.spawn, input.world)),
                Is.GreaterThan(input.world.tile_size * 0.1f));

            CollisionPlan collision = FindCollisionPlan(startCell, input);
            body.position = CellCenter(collision.adjacentCell, input.world);
            Physics2D.SyncTransforms();
            yield return new WaitForFixedUpdate();
            scriptedInput.Direction = collision.direction;
            for (int frame = 0; frame < 12; frame++)
            {
                yield return new WaitForFixedUpdate();
            }
            scriptedInput.Direction = Vector2.zero;
            yield return new WaitForFixedUpdate();

            if (collision.boundary)
            {
                AssertBoundaryStopped(body.position, collision.direction, input.world);
            }
            else
            {
                Vector2 blocker = CellCenter(collision.blockedCell, input.world);
                float separation = Vector2.Dot(blocker - body.position, collision.direction);
                Assert.That(separation, Is.GreaterThan(input.world.tile_size * 0.7f));
                Assert.That(separation, Is.LessThan(input.world.tile_size * 0.98f));
            }

            foreach (TestObjective objective in input.objectives.OrderBy(item => item.id, StringComparer.Ordinal))
            {
                body.position = CellCenter(objective.cell, input.world);
                Physics2D.SyncTransforms();
                yield return new WaitForFixedUpdate();
            }

            Assert.That(progress.CollectedCount, Is.EqualTo(progress.TotalObjectives));
            Assert.That(progress.IsWon, Is.False);
            body.position = CellCenter(input.exit.cell, input.world);
            Physics2D.SyncTransforms();
            yield return new WaitForFixedUpdate();
            Assert.That(progress.IsWon, Is.True);
        }

        private static Vector2Int FindOpenNeighbor(Vector2Int start, TestGameInput input)
        {
            HashSet<Vector2Int> blocked = WallCells(input);
            foreach (Vector2Int direction in Directions)
            {
                Vector2Int candidate = start + direction;
                if (IsInside(candidate, input.world) && !blocked.Contains(candidate))
                {
                    return candidate;
                }
            }
            return start;
        }

        private static CollisionPlan FindCollisionPlan(Vector2Int start, TestGameInput input)
        {
            HashSet<Vector2Int> blocked = WallCells(input);
            HashSet<Vector2Int> reachable = ReachableCells(start, input.world, blocked);
            foreach (TestWall wall in input.walls.OrderBy(item => item.id, StringComparer.Ordinal))
            {
                foreach (TestCell cell in wall.cells.OrderBy(item => item.y).ThenBy(item => item.x))
                {
                    Vector2Int blockedCell = ToGridCell(cell);
                    foreach (Vector2Int direction in Directions)
                    {
                        Vector2Int adjacent = blockedCell - direction;
                        if (reachable.Contains(adjacent))
                        {
                            return new CollisionPlan(adjacent, direction, blockedCell, false);
                        }
                    }
                }
            }

            foreach (Vector2Int cell in reachable.OrderBy(item => item.y).ThenBy(item => item.x))
            {
                if (cell.x == 0) return new CollisionPlan(cell, Vector2.left, cell, true);
                if (cell.x == input.world.width - 1) return new CollisionPlan(cell, Vector2.right, cell, true);
                if (cell.y == 0) return new CollisionPlan(cell, Vector2.down, cell, true);
                if (cell.y == input.world.height - 1) return new CollisionPlan(cell, Vector2.up, cell, true);
            }
            throw new InvalidDataException("gameplay_collision_probe_missing");
        }

        private static HashSet<Vector2Int> WallCells(TestGameInput input)
        {
            return new HashSet<Vector2Int>(input.walls.SelectMany(wall => wall.cells).Select(ToGridCell));
        }

        private static HashSet<Vector2Int> ReachableCells(
            Vector2Int start,
            TestWorld world,
            HashSet<Vector2Int> blocked
        )
        {
            var visited = new HashSet<Vector2Int> { start };
            var pending = new Queue<Vector2Int>();
            pending.Enqueue(start);
            while (pending.Count > 0)
            {
                Vector2Int current = pending.Dequeue();
                foreach (Vector2Int direction in Directions)
                {
                    Vector2Int neighbor = current + direction;
                    if (!IsInside(neighbor, world) || blocked.Contains(neighbor) || !visited.Add(neighbor))
                    {
                        continue;
                    }
                    pending.Enqueue(neighbor);
                }
            }
            return visited;
        }

        private static bool IsInside(Vector2Int cell, TestWorld world)
        {
            return cell.x >= 0 && cell.x < world.width && cell.y >= 0 && cell.y < world.height;
        }

        private static Vector2Int ToGridCell(TestCell cell)
        {
            return new Vector2Int(cell.x, cell.y);
        }

        private static Vector2 CellCenter(TestCell cell, TestWorld world)
        {
            return CellCenter(ToGridCell(cell), world);
        }

        private static Vector2 CellCenter(Vector2Int cell, TestWorld world)
        {
            float halfWidth = (world.width - 1) * world.tile_size * 0.5f;
            float halfHeight = (world.height - 1) * world.tile_size * 0.5f;
            return new Vector2(cell.x * world.tile_size - halfWidth, cell.y * world.tile_size - halfHeight);
        }

        private static void AssertBoundaryStopped(Vector2 position, Vector2 direction, TestWorld world)
        {
            float halfWidth = world.width * world.tile_size * 0.5f;
            float halfHeight = world.height * world.tile_size * 0.5f;
            float margin;
            if (direction.x < 0f) margin = position.x + halfWidth;
            else if (direction.x > 0f) margin = halfWidth - position.x;
            else if (direction.y < 0f) margin = position.y + halfHeight;
            else margin = halfHeight - position.y;
            Assert.That(margin, Is.GreaterThan(world.tile_size * 0.25f));
            Assert.That(margin, Is.LessThan(world.tile_size * 0.55f));
        }

    }

    internal sealed class CollisionPlan
    {
        public Vector2Int adjacentCell;
        public Vector2 direction;
        public Vector2Int blockedCell;
        public bool boundary;

        public CollisionPlan(Vector2Int adjacent, Vector2 movementDirection, Vector2Int blocked, bool isBoundary)
        {
            adjacentCell = adjacent;
            direction = movementDirection;
            blockedCell = blocked;
            boundary = isBoundary;
        }
    }

    internal sealed class ScriptedInputSource : IGameInputSource
    {
        public Vector2 Direction { get; set; }

        public Vector2 ReadMove()
        {
            return Direction;
        }
    }

    internal static class CollectionTraceWriter
    {
        public static void Write(IReadOnlyList<string> events)
        {
            string outputPath = Path.Combine(".book-production", "gameplay-trace.json");
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
            File.WriteAllText(outputPath, JsonUtility.ToJson(new TraceDocument(events)));
        }

        [Serializable]
        private sealed class TraceDocument
        {
            public List<string> events;

            public TraceDocument(IReadOnlyList<string> values)
            {
                events = new List<string>(values);
            }
        }
    }
}
