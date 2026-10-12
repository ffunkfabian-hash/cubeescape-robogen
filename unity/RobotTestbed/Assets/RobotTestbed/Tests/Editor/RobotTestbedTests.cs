using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using NUnit.Framework;
using UnityEditor;
using UnityEngine;

namespace RobotTestbed.Tests
{
    /// <summary>EditMode tests of the testbed: look format (same fixtures as viewer/test_viewer.sh), mask channel
    /// selection, the JSON reader and the prefabs the pipeline generated. Run after TestbedPipeline.Run.</summary>
    public class RobotTestbedTests
    {
        static string Project => Path.GetDirectoryName(Application.dataPath);
        static string Fixture(string name) => Path.GetFullPath(Path.Combine(Project, "..", "..", "viewer", "tests", name));

        static RobotConfig config;
        static RobotConfig Config => config ??= new RobotConfig(File.ReadAllText(
            Path.Combine(Project, Editor.TestbedImport.ImportDir, "robot_config.json")));

        static RobotLook Load(string fixture, List<string> warnings) =>
            RobotLook.Parse(File.ReadAllText(Fixture(fixture)), Config, warnings);

        [Test]
        public void V1LookMigratesToVersion3()
        {
            var l = Load("look_v1.json", new List<string>());
            Assert.AreEqual("TV", l.Variants["Head"]);
            Assert.AreEqual("Claw", l.Variants["Hands"]);
            Assert.AreEqual("Glanz", l.Finish);
            Assert.AreEqual("Custom", l.Skin);
            Assert.IsEmpty(l.Badges);
            Assert.AreEqual("None", l.LinePattern);
            Assert.IsFalse(l.AutoExpression);
        }

        [Test]
        public void V2LookKeepsItsColours()
        {
            var l = Load("look_v2.json", new List<string>());
            Assert.AreEqual("Custom", l.Skin);
            Assert.AreEqual(l.Colors["armor"], l.Armor2);
            Assert.IsEmpty(l.Zones2);
        }

        [Test]
        public void UnknownIdsOfVersion3AreReplaced()
        {
            var warnings = new List<string>();
            var l = Load("look_v3_unknown.json", warnings);
            Assert.AreEqual("Custom", l.Skin);
            CollectionAssert.AreEqual(new[] { "Pads" }, l.Zones2);
            CollectionAssert.AreEqual(new[] { "Cross", "Cloud" }, l.Badges.Select(b => b.id));
            Assert.AreEqual("None", l.LinePattern);
            Assert.AreEqual(2f, l.LineStrength);
            Assert.AreEqual("Damage", l.Expression);
            Assert.AreEqual("Heartbeat", l.Display);
            Assert.GreaterOrEqual(warnings.Count, 4);
        }

        [Test]
        public void FutureLookVersionIsRejected()
        {
            Assert.Throws<FormatException>(() => Load("look_future_version.json", new List<string>()));
        }

        [Test]
        public void SelectorPicksTheIdsChannel()
        {
            var badges = Config.List("badges");
            for (int i = 0; i < badges.Count; i++)
            {
                var b = (Dictionary<string, object>)badges[i];
                var sel = Config.Selector("badges", (string)b["id"]);
                Assert.AreEqual(1f, sel[i / 4][i % 4], (string)b["id"]);
                Assert.AreEqual(1f, sel.Sum(v => v.x + v.y + v.z + v.w), 1e-6, (string)b["id"]);
            }
        }

        [Test]
        public void EverySkinAppliesItsSystems()
        {
            foreach (var s in Config.Objects("skins"))
            {
                var look = new RobotLook();
                look.ApplySkin(s, true);
                Assert.AreEqual((string)s["name"], look.Skin);
                Assert.LessOrEqual(look.Badges.Count, RobotLook.MaxBadges);
                if (s.TryGetValue("parts", out var p))
                    foreach (var kv in (Dictionary<string, object>)p) Assert.AreEqual(kv.Value, look.Variants[kv.Key]);
            }
        }

        [Test]
        public void JsonReaderHandlesEscapesAndNumbers()
        {
            var d = (Dictionary<string, object>)Json.Parse("{\"a\": [1, -2.5e1, \"x\\\"y\\u0041\"], \"b\": true, \"c\": null}");
            var a = (List<object>)d["a"];
            Assert.AreEqual(1.0, a[0]);
            Assert.AreEqual(-25.0, a[1]);
            Assert.AreEqual("x\"yA", a[2]);
            Assert.AreEqual(true, d["b"]);
            Assert.IsNull(d["c"]);
        }

        [Test]
        public void GeneratedPrefabsUseTheRobotShaders()
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(Editor.TestbedPipeline.GenDir + "/Robot_Main.prefab");
            Assert.IsNotNull(prefab, "run TestbedPipeline.Run first");
            var shaders = prefab.GetComponentsInChildren<Renderer>(true).SelectMany(r => r.sharedMaterials)
                .Select(m => m.shader.name).Distinct().ToList();
            CollectionAssert.IsSubsetOf(shaders, new[] { "CubeEscape/RobotBody", "CubeEscape/RobotScreen" });
            Assert.IsTrue(prefab.TryGetComponent(out RobotAppearance _));
            Assert.IsTrue(prefab.TryGetComponent(out RobotFace _));
        }
    }
}
