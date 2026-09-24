using System;
using System.IO;
using System.Security.Cryptography;
using BookProduction.Editor;
using NUnit.Framework;

namespace BookProduction.Tests.EditMode
{
    public sealed class SceneVerifierTests
    {
        [SetUp]
        public void BuildSmokeScene()
        {
            SceneCompiler.BuildFromGameIR();
        }

        [Test]
        public void GeneratedSceneHasExpectedGameplayHierarchy()
        {
            SceneVerificationReport report = SceneVerifier.VerifyGeneratedScene();

            Assert.That(report.PlayerCount, Is.EqualTo(1));
            Assert.That(report.WallColliderCount, Is.GreaterThan(0));
            Assert.That(report.ObjectiveCount, Is.EqualTo(1));
            Assert.That(report.ExitCount, Is.EqualTo(1));
            Assert.That(report.UniqueEntityIds, Is.True);
            Assert.That(report.ExternalResourceCount, Is.Zero);
            Assert.That(report.DisallowedComponentCount, Is.Zero);
            Assert.That(report.TrustedTemplateNoNetworkReferences, Is.True);
            Assert.That(
                File.Exists(Path.Combine(Directory.GetCurrentDirectory(), ".book-production", "worker-report.json")),
                Is.True
            );
        }

        [Test]
        public void RepeatedCompilationProducesByteIdenticalScene()
        {
            SceneCompiler.BuildFromGameIR();
            string firstHash = HashScene();

            SceneCompiler.BuildFromGameIR();
            string secondHash = HashScene();

            Assert.That(secondHash, Is.EqualTo(firstHash));
        }

        private static string HashScene()
        {
            using (SHA256 hash = SHA256.Create())
            {
                byte[] digest = hash.ComputeHash(File.ReadAllBytes(SceneCompiler.GeneratedScenePath));
                return BitConverter.ToString(digest).Replace("-", string.Empty);
            }
        }
    }
}
