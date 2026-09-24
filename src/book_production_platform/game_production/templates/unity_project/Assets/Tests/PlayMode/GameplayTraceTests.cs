using System.Collections;
using System.Collections.Generic;
using System.IO;
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
            SceneTestDriver.BeginLoadGeneratedScene();
            yield return null;
            GameProgress progress = SceneTestDriver.FindGeneratedProgress();
            Assert.That(progress, Is.Not.Null);
            progress.TryWin("exit");
            Assert.That(progress.IsWon, Is.False);
            Assert.That(progress.Events, Does.Contain("exit_blocked:exit"));

            yield return SceneTestDriver.PlayCollectionTrace(progress);

            Assert.That(progress.CollectedCount, Is.EqualTo(progress.TotalObjectives));
            Assert.That(progress.IsWon, Is.True);
            CollectionAssert.AreEqual(
                new[] { "exit_blocked:exit", "player_moved", "objective_collected:objective-a", "game_won:exit" },
                progress.Events
            );
            CollectionTraceWriter.Write(progress.Events);
            Assert.That(
                File.Exists(Path.Combine(Directory.GetCurrentDirectory(), ".book-production", "gameplay-trace.json")),
                Is.True
            );
        }
    }

    internal static class SceneTestDriver
    {
        public static void BeginLoadGeneratedScene()
        {
            SceneManager.LoadScene("smoke-room", LoadSceneMode.Single);
        }

        public static GameProgress FindGeneratedProgress()
        {
            return Object.FindAnyObjectByType<GameProgress>();
        }

        public static IEnumerator PlayCollectionTrace(GameProgress progress)
        {
            TopDownPlayerController controller = Object.FindAnyObjectByType<TopDownPlayerController>();
            var input = new ScriptedInputSource();
            controller.InputSource = input;

            GameObject wall = GameObject.Find("Wall-wall-a-3-1");
            yield return MoveTo(controller, input, new Vector2(wall.transform.position.x - 0.9f, controller.transform.position.y));
            Assert.That(controller.transform.position.x, Is.LessThan(wall.transform.position.x));

            yield return MoveTo(controller, input, new Vector2(controller.transform.position.x, wall.transform.position.y + 1f));
            GameObject objective = GameObject.Find("Objective-objective-a");
            yield return MoveTo(controller, input, new Vector2(objective.transform.position.x, controller.transform.position.y));
            yield return MoveTo(controller, input, objective.transform.position);

            GameObject exit = GameObject.Find("Exit-exit");
            yield return MoveTo(controller, input, exit.transform.position);
            input.Direction = Vector2.zero;
            yield return new WaitForFixedUpdate();
        }

        private static IEnumerator MoveTo(
            TopDownPlayerController controller,
            ScriptedInputSource input,
            Vector2 target
        )
        {
            int frames = 0;
            while (Vector2.Distance(controller.transform.position, target) > 0.05f && frames++ < 400)
            {
                Vector2 delta = target - (Vector2)controller.transform.position;
                input.Direction = new Vector2(Mathf.Sign(delta.x), Mathf.Sign(delta.y));
                yield return new WaitForFixedUpdate();
            }

            input.Direction = Vector2.zero;
            Assert.That(Vector2.Distance(controller.transform.position, target), Is.LessThanOrEqualTo(0.1f));
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

        [System.Serializable]
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
