using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace RobotTestbed.Editor
{
    /// <summary>Screenshots of the URP result (the generator's previews are Blender/EEVEE): every skin, every robot type,
    /// every expression and a few animation frames, written to ROBOT_REPORT/shots. Batch mode renders into its own
    /// RenderTexture (the batch screen is only 640 x 480).</summary>
    public static class TestbedShots
    {
        static Camera cam;

        public static void Render(TestbedPipeline.Prefabs p)
        {
            string dir = Path.Combine(TestbedPipeline.ReportDir, "shots");
            if (Directory.Exists(dir)) Directory.Delete(dir, true);
            EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
            BuildStage();
            int n = 0;

            var modular = (GameObject)PrefabUtility.InstantiatePrefab(p.Modular);
            var app = modular.GetComponent<RobotAppearance>();
            var face = modular.GetComponent<RobotFace>();
            foreach (var skin in p.Config.Objects("skins"))
            {
                var look = new RobotLook();
                foreach (var slot in p.Config.Objects("slots"))
                    look.Variants[(string)slot["id"]] = (string)((List<object>)slot["variants"])[0];
                look.ApplySkin(skin, true);
                app.Apply(look);
                app.ShowVariants();
                face.Apply(look);
                Card();
                n += Shot(Path.Combine(dir, "skins", (string)skin["name"] + ".png"), 420, 460);
            }
            Object.DestroyImmediate(modular);

            foreach (var kv in p.Types)
            {
                var go = (GameObject)PrefabUtility.InstantiatePrefab(kv.Value);
                string lookPath = TestbedImport.ImportDir + "/Looks/" + kv.Key + ".robotlook.json";
                var look = RobotLook.Parse(File.ReadAllText(lookPath), p.Config, new List<string>());
                go.GetComponent<RobotAppearance>().Apply(look);
                go.GetComponent<RobotFace>().Apply(look);
                Card();
                n += Shot(Path.Combine(dir, "types", kv.Key + ".png"), 420, 460);
                Object.DestroyImmediate(go);
            }

            var main = (GameObject)PrefabUtility.InstantiatePrefab(p.Main);
            var mainLook = new RobotLook();
            mainLook.ApplySkin(p.Config.Skin("Classic"));
            main.GetComponent<RobotAppearance>().Apply(mainLook);
            var mainFace = main.GetComponent<RobotFace>();
            mainFace.Apply(mainLook);
            Aim(new Vector3(0.25f, 1.16f, 1.25f), new Vector3(0f, 1.1f, 0f), 40f);
            foreach (var e in p.Config.Strings("expressions"))
            {
                mainFace.SetExpression(e);
                n += Shot(Path.Combine(dir, "expressions", e + ".png"), 300, 240);
            }
            Card();
            foreach (var (clipName, frames) in new[] { ("Walk", new[] { 0, 5, 10, 15 }), ("ClimbUp", new[] { 0, 20, 40, 60 }),
                                                       ("DanceRobot", new[] { 0, 12, 24, 36 }), ("Death", new[] { 0, 20, 40, 60 }) })
            {
                var clip = p.Clips.FirstOrDefault(c => c.name == clipName);
                if (clip == null) continue;
                foreach (int f in frames)
                {
                    clip.SampleAnimation(main, Mathf.Min(f / 30f, clip.length));
                    n += Shot(Path.Combine(dir, "anim", clipName + "_" + f.ToString("000") + ".png"), 320, 352);
                }
            }
            Object.DestroyImmediate(main);
            TestbedPipeline.Check("shots: rendered (" + n + ")", n > 0, n);
        }

        static void BuildStage()
        {
            var camGo = new GameObject("Testbed_Camera");
            cam = camGo.AddComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.06f, 0.087f, 0.125f);
            cam.nearClipPlane = 0.05f;
            cam.allowHDR = true;
            var data = camGo.AddComponent<UniversalAdditionalCameraData>();
            data.renderPostProcessing = true;
            var profile = ScriptableObject.CreateInstance<VolumeProfile>();
            var bloom = profile.Add<Bloom>(true);
            bloom.threshold.Override(1.1f);
            bloom.intensity.Override(0.5f);
            bloom.scatter.Override(0.55f);
            profile.Add<Tonemapping>(true).mode.Override(TonemappingMode.Neutral);
            var vol = new GameObject("Testbed_Volume").AddComponent<Volume>();
            vol.isGlobal = true;
            vol.sharedProfile = profile;

            Light Dir(string name, Vector3 euler, Color color, float intensity, LightShadows shadows)
            {
                var l = new GameObject(name).AddComponent<Light>();
                l.type = LightType.Directional;
                l.transform.rotation = Quaternion.Euler(euler);
                l.color = color;
                l.intensity = intensity;
                l.shadows = shadows;
                return l;
            }
            Dir("Key", new Vector3(45f, 150f, 0f), new Color(1f, 0.94f, 0.86f), 1.6f, LightShadows.Soft);
            Dir("Rim", new Vector3(20f, -20f, 0f), new Color(0.45f, 0.85f, 1f), 1.2f, LightShadows.None);
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = new Color(0.32f, 0.38f, 0.46f);
            RenderSettings.ambientEquatorColor = new Color(0.2f, 0.22f, 0.26f);
            RenderSettings.ambientGroundColor = new Color(0.08f, 0.08f, 0.09f);

            var floor = GameObject.CreatePrimitive(PrimitiveType.Plane);
            floor.name = "Testbed_Floor";
            floor.transform.localScale = new Vector3(3f, 1f, 3f);
            var fm = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            fm.SetColor("_BaseColor", new Color(0.09f, 0.11f, 0.13f));
            fm.SetFloat("_Smoothness", 0.45f);
            floor.GetComponent<Renderer>().sharedMaterial = fm;
        }

        static void Aim(Vector3 pos, Vector3 target, float fov)
        {
            cam.transform.position = pos;
            cam.transform.LookAt(target);
            cam.fieldOfView = fov;
        }

        /// <summary>Three-quarter view like the generator's skin cards (robot looks +Z, its right side is +X).</summary>
        static void Card() => Aim(new Vector3(1.03f, 1.0f, 2.2f), new Vector3(-0.02f, 0.66f, 0f), 34.5f);

        static int Shot(string path, int w, int h)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            var hdr = new RenderTexture(w, h, 24, RenderTextureFormat.DefaultHDR) { antiAliasing = 4 };
            var ldr = new RenderTexture(w, h, 0);
            var prev = RenderTexture.active;
            var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
            try
            {
                cam.targetTexture = hdr;
                cam.Render();
                cam.Render();
                Graphics.Blit(hdr, ldr);
                RenderTexture.active = ldr;
                tex.ReadPixels(new Rect(0, 0, w, h), 0, 0);
                tex.Apply();
                File.WriteAllBytes(path, tex.EncodeToPNG());
                return 1;
            }
            finally
            {
                cam.targetTexture = null;
                RenderTexture.active = prev;
                Object.DestroyImmediate(tex);
                hdr.Release();
                ldr.Release();
            }
        }
    }
}
