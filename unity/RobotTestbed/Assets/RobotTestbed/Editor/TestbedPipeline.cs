using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEditor.Animations;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

namespace RobotTestbed.Editor
{
    /// <summary>Batch entry point (unity/run_testbed.sh): copies the generator output into the project, builds URP
    /// settings, materials, prefabs and an animator, checks the import and writes report.json plus screenshots.
    /// Environment: ROBOT_OUTPUT (generator output folder), ROBOT_REPORT (report folder).</summary>
    public static class TestbedPipeline
    {
        public const string GenDir = "Assets/RobotTestbed/Generated";
        public static readonly string[] Textures =
        {
            "T_Robot_BaseColor.png", "T_Robot_Emission.png", "T_Robot_MetallicSmoothness.png", "T_Robot_Normal.png",
            "T_Robot_Occlusion.png", "T_Robot_ColorMask.png", "T_Robot_LightMask.png", "T_Robot_BadgeMask.png",
            "T_Robot_BadgeMask2.png", "T_Robot_BadgeMask3.png", "T_Robot_LineMask.png", "T_Robot_LineMask2.png", "T_Robot_ZoneMask.png",
            "T_Robot_ZoneMask2.png", "T_Robot_LayerMask.png", "T_Robot_LayerMask2.png", "T_Robot_MaterialMask.png",
            "T_Robot_MaterialMask2.png", "T_Robot_EyeAtlas.png", "T_Robot_DisplayAtlas.png", "T_Robot_DisplayFlipbook.png",
        };
        static readonly string[] RequiredBones =
        {
            "Root", "Pelvis", "Spine", "Head", "Shoulder_L", "Shoulder_R", "UpperArm_L", "UpperArm_R", "LowerArm_L",
            "LowerArm_R", "Hand_L", "Hand_R", "UpperLeg_L", "UpperLeg_R", "LowerLeg_L", "LowerLeg_R", "Foot_L", "Foot_R",
        };

        static readonly List<(string name, bool ok, string detail)> Checks = new List<(string, bool, string)>();

        public static void Check(string name, bool ok, object detail = null)
        {
            Checks.Add((name, ok, detail == null ? "" : detail.ToString()));
            Debug.Log((ok ? "PASS " : "FAIL ") + name + (detail != null ? " - " + detail : ""));
        }

        public static string ReportDir => Environment.GetEnvironmentVariable("ROBOT_REPORT") ??
                                          Path.Combine(Path.GetDirectoryName(Application.dataPath), "Logs", "TestbedReport");

        /// <summary>-executeMethod RobotTestbed.Editor.TestbedPipeline.Run</summary>
        public static void Run()
        {
            int code = 1;
            var t0 = DateTime.Now;
            try
            {
                Checks.Clear();
                string output = Environment.GetEnvironmentVariable("ROBOT_OUTPUT");
                if (string.IsNullOrEmpty(output) || !Directory.Exists(output))
                    throw new ArgumentException("ROBOT_OUTPUT must name the generator output folder");
                Directory.CreateDirectory(ReportDir);
                var copied = CopyOutput(output);
                Debug.Log("testbed: " + copied.Count + " files copied from " + output);
                AssetDatabase.Refresh(ImportAssetOptions.ForceSynchronousImport);
                if (copied.Contains("robot_config.json"))       // clip settings come from it (TestbedImport)
                    AssetDatabase.ImportAsset(TestbedImport.MainFbx, ImportAssetOptions.ForceUpdate);
                SetMotionNode();
                SetTypeAvatars();
                EnsurePipeline();
                var mats = BuildMaterials();
                var prefabs = BuildPrefabs(mats);
                RunChecks(prefabs);
                TestbedShots.Render(prefabs);
                code = Checks.All(c => c.ok) ? 0 : 1;
            }
            catch (Exception e)
            {
                Check("pipeline ran without exception", false, e.ToString().Split('\n')[0]);
                Debug.LogException(e);
            }
            WriteReport((DateTime.Now - t0).TotalSeconds);
            EditorApplication.Exit(code);
        }

        /// <summary>Copies only changed files (same size and time = unchanged, they keep their import) and removes
        /// files the generator no longer writes (a removed type or look). Returns the copied relative paths.</summary>
        static List<string> CopyOutput(string output)
        {
            string dst = Path.Combine(Path.GetDirectoryName(Application.dataPath), TestbedImport.ImportDir);
            Directory.CreateDirectory(dst);
            var files = new List<(string src, string rel)>
            {
                (Path.Combine(output, "CubeEscape_Robot.fbx"), "CubeEscape_Robot.fbx"),
                (Path.Combine(output, "CubeEscape_Robot_Modular.fbx"), "CubeEscape_Robot_Modular.fbx"),
                (Path.Combine(output, "robot_config.json"), "robot_config.json"),
            };
            foreach (var t in Textures) files.Add((Path.Combine(output, "Textures", t), "Textures/" + t));
            foreach (var f in Directory.GetFiles(Path.Combine(output, "Types"), "*.fbx"))
                files.Add((f, "Types/" + Path.GetFileName(f)));
            foreach (var f in Directory.GetFiles(Path.Combine(output, "Looks"), "*.robotlook.json"))
                files.Add((f, "Looks/" + Path.GetFileName(f)));
            var copied = new List<string>();
            foreach (var (src, rel) in files)
            {
                if (!File.Exists(src)) throw new FileNotFoundException("generator output incomplete", src);
                string to = Path.Combine(dst, rel);
                Directory.CreateDirectory(Path.GetDirectoryName(to));
                var a = new FileInfo(src);
                var b = new FileInfo(to);
                if (b.Exists && b.Length == a.Length && b.LastWriteTimeUtc == a.LastWriteTimeUtc) continue;
                File.Copy(src, to, true);
                File.SetLastWriteTimeUtc(to, a.LastWriteTimeUtc);
                copied.Add(rel);
            }
            var wanted = new HashSet<string>(files.Select(f => Path.GetFullPath(Path.Combine(dst, f.rel))));
            foreach (string sub in new[] { "Types", "Looks", "Textures" })
                foreach (var f in Directory.GetFiles(Path.Combine(dst, sub)))
                    if (!f.EndsWith(".meta") && !wanted.Contains(Path.GetFullPath(f)))
                    {
                        File.Delete(f);
                        if (File.Exists(f + ".meta")) File.Delete(f + ".meta");
                    }
            return copied;
        }

        public static string BonePath(Transform root, string bone)
        {
            var t = root.GetComponentsInChildren<Transform>(true).FirstOrDefault(x => x.name == bone);
            return t == null ? null : AnimationUtility.CalculateTransformPath(t, root);
        }

        /// <summary>Generic rigs need the root motion node by path (README: Root node = Root).</summary>
        static void SetMotionNode()
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(TestbedImport.MainFbx);
            string path = BonePath(model.transform, "Root");
            var mi = (ModelImporter)AssetImporter.GetAtPath(TestbedImport.MainFbx);
            Check("fbx: Root bone found", path != null, path);
            if (path != null && mi.motionNodeName != path)
            {
                mi.motionNodeName = path;
                mi.SaveAndReimport();
            }
        }

        /// <summary>Modular and type FBX use the avatar of the main FBX (README: Copy From Other Avatar).</summary>
        static void SetTypeAvatars()
        {
            var avatar = AssetDatabase.LoadAllAssetsAtPath(TestbedImport.MainFbx).OfType<Avatar>().FirstOrDefault();
            Check("fbx: main avatar valid", avatar != null && avatar.isValid, avatar != null ? avatar.name : "none");
            var paths = new List<string> { TestbedImport.ImportDir + "/CubeEscape_Robot_Modular.fbx" };
            paths.AddRange(AssetDatabase.FindAssets("t:Model", new[] { TestbedImport.ImportDir + "/Types" })
                .Select(AssetDatabase.GUIDToAssetPath));
            foreach (var p in paths)
            {
                var mi = (ModelImporter)AssetImporter.GetAtPath(p);
                if (mi.avatarSetup == ModelImporterAvatarSetup.CopyFromOther && mi.sourceAvatar == avatar) continue;
                mi.avatarSetup = ModelImporterAvatarSetup.CopyFromOther;
                mi.sourceAvatar = avatar;
                mi.SaveAndReimport();
            }
        }

        /// <summary>URP asset (forward renderer, linear colour space) for the testbed project.</summary>
        static void EnsurePipeline()
        {
            Directory.CreateDirectory(GenDir);
            string assetPath = GenDir + "/URP.asset";
            var urp = AssetDatabase.LoadAssetAtPath<UniversalRenderPipelineAsset>(assetPath);
            if (urp == null)
            {
                var renderer = ScriptableObject.CreateInstance<UniversalRendererData>();
                renderer.renderingMode = RenderingMode.Forward;
                AssetDatabase.CreateAsset(renderer, GenDir + "/URP_Renderer.asset");
                urp = UniversalRenderPipelineAsset.Create(renderer);
                urp.shadowDistance = 12f;
                urp.supportsHDR = true;
                AssetDatabase.CreateAsset(urp, assetPath);
                AssetDatabase.SaveAssets();
            }
            GraphicsSettings.defaultRenderPipeline = urp;
            int level = QualitySettings.GetQualityLevel();
            for (int i = 0; i < QualitySettings.names.Length; i++)
            {
                QualitySettings.SetQualityLevel(i, false);
                QualitySettings.renderPipeline = urp;
            }
            QualitySettings.SetQualityLevel(level, false);
            PlayerSettings.colorSpace = ColorSpace.Linear;
            Check("urp: pipeline asset active", GraphicsSettings.currentRenderPipeline == urp);
        }

        static Texture2D Tex(string name) =>
            AssetDatabase.LoadAssetAtPath<Texture2D>(TestbedImport.ImportDir + "/Textures/" + name);

        public sealed class Materials
        {
            public Material Body, Fabric, Eyes, Display;
        }

        static Material Mat(string name, Shader shader)
        {
            string path = GenDir + "/" + name + ".mat";
            var m = AssetDatabase.LoadAssetAtPath<Material>(path);
            if (m == null)
            {
                m = new Material(shader);
                AssetDatabase.CreateAsset(m, path);
            }
            m.shader = shader;
            return m;
        }

        static Materials BuildMaterials()
        {
            var body = Shader.Find("CubeEscape/RobotBody");
            var screen = Shader.Find("CubeEscape/RobotScreen");
            Check("shader: RobotBody compiles", body != null && !ShaderUtil.ShaderHasError(body));
            Check("shader: RobotScreen compiles", screen != null && !ShaderUtil.ShaderHasError(screen));
            var mats = new Materials
            {
                Body = Mat("M_RobotBody", body), Fabric = Mat("M_RobotFabric", body),
                Eyes = Mat("M_RobotEyes", screen), Display = Mat("M_RobotDisplay", screen),
            };
            foreach (var m in new[] { mats.Body, mats.Fabric })
            {
                m.SetTexture("_BaseMap", Tex("T_Robot_BaseColor.png"));
                m.SetTexture("_MetallicSmoothness", Tex("T_Robot_MetallicSmoothness.png"));
                m.SetTexture("_BumpMap", Tex("T_Robot_Normal.png"));
                m.SetTexture("_OcclusionMap", Tex("T_Robot_Occlusion.png"));
                m.SetTexture("_EmissionMap", Tex("T_Robot_Emission.png"));
                m.SetTexture("_ColorMask", Tex("T_Robot_ColorMask.png"));
                m.SetTexture("_LightMask", Tex("T_Robot_LightMask.png"));
                m.SetTexture("_BadgeMask", Tex("T_Robot_BadgeMask.png"));
                m.SetTexture("_BadgeMask2", Tex("T_Robot_BadgeMask2.png"));
                m.SetTexture("_BadgeMask3", Tex("T_Robot_BadgeMask3.png"));
                m.SetTexture("_LineMask", Tex("T_Robot_LineMask.png"));
                m.SetTexture("_LineMask2", Tex("T_Robot_LineMask2.png"));
                m.SetTexture("_ZoneMask", Tex("T_Robot_ZoneMask.png"));
                m.SetTexture("_ZoneMask2", Tex("T_Robot_ZoneMask2.png"));
                m.SetTexture("_LayerMask", Tex("T_Robot_LayerMask.png"));
                m.SetTexture("_LayerMask2", Tex("T_Robot_LayerMask2.png"));
                m.SetTexture("_MaterialMask", Tex("T_Robot_MaterialMask.png"));
                m.SetTexture("_MaterialMask2", Tex("T_Robot_MaterialMask2.png"));
            }
            mats.Fabric.SetFloat("_Fabric", 1f);
            mats.Eyes.SetTexture("_Atlas", Tex("T_Robot_EyeAtlas.png"));
            mats.Eyes.SetFloat("_Mode", 0f);
            mats.Eyes.SetFloat("_SrcBlend", (float)BlendMode.One);
            mats.Eyes.SetFloat("_DstBlend", (float)BlendMode.One);
            mats.Eyes.SetFloat("_ZWrite", 0f);
            mats.Eyes.renderQueue = (int)RenderQueue.Transparent;
            mats.Display.SetTexture("_Atlas", Tex("T_Robot_DisplayAtlas.png"));
            mats.Display.SetFloat("_Mode", 1f);
            mats.Display.SetFloat("_SrcBlend", (float)BlendMode.One);
            mats.Display.SetFloat("_DstBlend", (float)BlendMode.Zero);
            mats.Display.SetFloat("_ZWrite", 1f);
            mats.Display.renderQueue = (int)RenderQueue.Geometry;
            AssetDatabase.SaveAssets();
            return mats;
        }

        public sealed class Prefabs
        {
            public GameObject Main, Modular;
            public readonly Dictionary<string, GameObject> Types = new Dictionary<string, GameObject>();
            public AnimatorController Controller;
            public List<AnimationClip> Clips;
            public RobotConfig Config;
            public TextAsset ConfigAsset;
        }

        /// <summary>FBX material names (M_Robot, M_RobotFabric, M_RobotEyes, M_RobotDisplay) -> testbed materials.</summary>
        static void Remap(GameObject go, Materials mats)
        {
            foreach (var r in go.GetComponentsInChildren<Renderer>(true))
            {
                var list = r.sharedMaterials;
                for (int i = 0; i < list.Length; i++)
                {
                    string n = list[i] != null ? list[i].name : "";
                    list[i] = n.StartsWith("M_RobotFabric") ? mats.Fabric : n.StartsWith("M_RobotEyes") ? mats.Eyes
                        : n.StartsWith("M_RobotDisplay") ? mats.Display : mats.Body;
                }
                r.sharedMaterials = list;
            }
        }

        static GameObject Prefab(string fbx, string name, Materials mats, Prefabs p)
        {
            var model = AssetDatabase.LoadAssetAtPath<GameObject>(fbx);
            var go = (GameObject)PrefabUtility.InstantiatePrefab(model);
            go.name = name;
            Remap(go, mats);
            if (!go.TryGetComponent(out Animator anim)) anim = go.AddComponent<Animator>();   // no ?? on Unity objects
            anim.runtimeAnimatorController = p.Controller;
            go.AddComponent<RobotAppearance>().Init(p.ConfigAsset);
            go.AddComponent<RobotFace>().Init(p.ConfigAsset, Tex("T_Robot_DisplayAtlas.png"), Tex("T_Robot_DisplayFlipbook.png"));
            var prefab = PrefabUtility.SaveAsPrefabAsset(go, GenDir + "/" + name + ".prefab");
            UnityEngine.Object.DestroyImmediate(go);
            return prefab;
        }

        static Prefabs BuildPrefabs(Materials mats)
        {
            var p = new Prefabs();
            p.ConfigAsset = AssetDatabase.LoadAssetAtPath<TextAsset>(TestbedImport.ImportDir + "/robot_config.json");
            p.Config = new RobotConfig(p.ConfigAsset.text);
            p.Clips = AssetDatabase.LoadAllAssetsAtPath(TestbedImport.MainFbx).OfType<AnimationClip>()
                .Where(c => !c.name.StartsWith("__preview__")).OrderBy(c => c.name).ToList();
            string ctrlPath = GenDir + "/Robot.controller";
            AssetDatabase.DeleteAsset(ctrlPath);
            p.Controller = AnimatorController.CreateAnimatorControllerAtPath(ctrlPath);
            var sm = p.Controller.layers[0].stateMachine;
            foreach (var clip in p.Clips)
            {
                var st = sm.AddState(clip.name);
                st.motion = clip;
                if (clip.name == "Idle") sm.defaultState = st;
            }
            p.Main = Prefab(TestbedImport.MainFbx, "Robot_Main", mats, p);
            p.Modular = Prefab(TestbedImport.ImportDir + "/CubeEscape_Robot_Modular.fbx", "Robot_Modular", mats, p);
            foreach (var t in p.Config.Objects("robotTypes"))
            {
                string name = (string)t["name"];
                string fbx = TestbedImport.ImportDir + "/Types/CubeEscape_Robot_" + name + ".fbx";
                if (File.Exists(fbx)) p.Types[name] = Prefab(fbx, "Robot_Type_" + name, mats, p);
            }
            AssetDatabase.SaveAssets();
            return p;
        }

        /// <summary>1.12: spring bones, emitters and type emotes arrive in Unity and move.</summary>
        static void CheckBoltOn(Prefabs p, HashSet<string> bones)
        {
            var cfg = p.Config;
            var springs = cfg.List("springBones").Select(o => (string)o).ToList();
            var emitters = cfg.List("emitters").Cast<Dictionary<string, object>>().ToList();
            var need = springs.Concat(emitters.Select(e => (string)e["bone"])).Where(b => !bones.Contains(b)).ToList();
            Check("fbx: spring bones and emitter sockets present (" + springs.Count + " + " + emitters.Count + ")",
                  springs.Count > 0 && emitters.Count > 0 && need.Count == 0, string.Join(",", need));
            // largest deviation from the rest pose (Idle frame 0) during a clip
            float Swing(string type, string clip, string bone, out float moved)
            {
                moved = 0f;
                var c = p.Clips.FirstOrDefault(x => x.name == clip);
                var idle = p.Clips.FirstOrDefault(x => x.name == "Idle");
                if (c == null || idle == null || !p.Types.TryGetValue(type, out var prefab)) return -1f;
                var go = UnityEngine.Object.Instantiate(prefab);
                try
                {
                    var t = go.GetComponentsInChildren<Transform>(true).First(x => x.name == bone);
                    idle.SampleAnimation(go, 0f);
                    Quaternion r0 = t.localRotation;
                    Vector3 p0 = t.position;
                    float worst = 0f;
                    for (float s = 0f; s <= c.length + 1e-4f; s += 2f / 30f)
                    {
                        c.SampleAnimation(go, s);
                        worst = Mathf.Max(worst, Quaternion.Angle(r0, t.localRotation));
                        moved = Mathf.Max(moved, (t.position - p0).magnitude);
                    }
                    return worst;
                }
                finally { UnityEngine.Object.DestroyImmediate(go); }
            }
            float cape = Swing("King", "Run", "Cape", out _);
            float antenna = Swing("Scout", "Jump", "Antenna", out _);
            Check("springs: cape lifts in Run (> 10 deg), antenna swings in Jump (> 3 deg)", cape > 10f && antenna > 3f,
                  "cape " + cape.ToString("0.0", CultureInfo.InvariantCulture) + " deg, antenna " +
                  antenna.ToString("0.0", CultureInfo.InvariantCulture) + " deg");
            Swing("Gentleman", "HatTip", "Socket_Head", out float hat);
            Check("emotes: HatTip lifts the hat of the Gentleman (> 0.2 m)", hat > 0.2f,
                  hat.ToString("0.00", CultureInfo.InvariantCulture) + " m");
        }

        static void RunChecks(Prefabs p)
        {
            var cfg = p.Config;
            var bones = p.Main.GetComponentsInChildren<Transform>(true).Select(t => t.name).ToHashSet();
            var missing = RequiredBones.Where(b => !bones.Contains(b)).ToList();
            Check("fbx: required bones present", missing.Count == 0, missing.Count == 0 ? bones.Count + " transforms" : string.Join(",", missing));
            var clipMeta = (Dictionary<string, object>)cfg.Root["clips"];
            var names = p.Clips.Select(c => c.name).ToHashSet();
            var missingClips = clipMeta.Keys.Where(k => !names.Contains(k)).ToList();
            Check("fbx: every clip imported (" + clipMeta.Count + ")", missingClips.Count == 0 && p.Clips.Count == clipMeta.Count,
                  missingClips.Count == 0 ? p.Clips.Count + " clips" : string.Join(",", missingClips));
            var wrongLength = new List<string>();
            var wrongLoop = new List<string>();
            foreach (var clip in p.Clips)
            {
                if (!clipMeta.TryGetValue(clip.name, out var m)) continue;
                var meta = (Dictionary<string, object>)m;
                float expect = (float)((double)meta["frames"] / (double)meta["fps"]);
                if (Mathf.Abs(clip.length - expect) > 1.01f / (float)(double)meta["fps"]) wrongLength.Add(clip.name + " " + clip.length.ToString("0.00", CultureInfo.InvariantCulture));
                if (clip.isLooping != (bool)meta["loop"]) wrongLoop.Add(clip.name);
            }
            Check("clips: length = frames / fps", wrongLength.Count == 0, string.Join(",", wrongLength));
            Check("clips: loop flags from robot_config.json", wrongLoop.Count == 0, string.Join(",", wrongLoop));
            var mi = (ModelImporter)AssetImporter.GetAtPath(TestbedImport.MainFbx);
            var badRoot = mi.clipAnimations.Where(c =>
                c.lockRootRotation == TestbedImport.RootRotation.Contains(c.name) ||
                c.lockRootPositionXZ == TestbedImport.RootPosition.Contains(c.name)).Select(c => c.name).ToList();
            Check("clips: root motion only for turns and ClimbUp", badRoot.Count == 0, string.Join(",", badRoot));
            CheckTextures();
            Check("prefabs: one per robot type", p.Types.Count == cfg.List("robotTypes").Count, p.Types.Count);
            CheckPose(p);
            CheckBoltOn(p, bones);
            CheckLooks(p);
        }

        static void CheckTextures()
        {
            var bad = new List<string>();
            foreach (var t in Textures)
            {
                var ti = (TextureImporter)AssetImporter.GetAtPath(TestbedImport.ImportDir + "/Textures/" + t);
                bool color = t.StartsWith("T_Robot_BaseColor") || t.StartsWith("T_Robot_Emission");
                if (ti == null || (t == "T_Robot_Normal.png" ? ti.textureType != TextureImporterType.NormalMap
                        : ti.sRGBTexture != color || ti.alphaIsTransparency))
                    bad.Add(t);
            }
            Check("textures: colour sRGB, data linear, alpha is no transparency", bad.Count == 0, string.Join(",", bad));
            var eye = Tex("T_Robot_EyeAtlas.png");
            Check("textures: eye atlas keeps 2048 x 2560", eye != null && eye.width == 2048 && eye.height == 2560,
                  eye != null ? eye.width + "x" + eye.height : "missing");
        }

        /// <summary>Rest pose height and every clip sampled: no vertex below the floor (like the generator QA).</summary>
        static void CheckPose(Prefabs p)
        {
            var go = (GameObject)PrefabUtility.InstantiatePrefab(p.Main);
            try
            {
                var smr = go.GetComponentInChildren<SkinnedMeshRenderer>();
                var mesh = new Mesh();
                float MinY(out float maxY)
                {
                    smr.BakeMesh(mesh, true);
                    var m = smr.transform.localToWorldMatrix;
                    float lo = float.MaxValue, hi = float.MinValue;
                    foreach (var v in mesh.vertices)
                    {
                        float y = m.MultiplyPoint3x4(v).y;
                        lo = Mathf.Min(lo, y);
                        hi = Mathf.Max(hi, y);
                    }
                    maxY = hi;
                    return lo;
                }
                // the FBX keeps the pose of the last exported take; the game starts in Idle, so measure Idle frame 0
                var idle = p.Clips.First(c => c.name == "Idle");
                idle.SampleAnimation(go, 0f);
                float rest = MinY(out float top);
                Check("pose: Idle frame 0 stands on y = 0, height 1.2-1.5 m", Mathf.Abs(rest) < 0.01f && top - rest > 1.2f && top - rest < 1.5f,
                      "min " + rest.ToString("0.000", CultureInfo.InvariantCulture) + ", height " + (top - rest).ToString("0.000", CultureInfo.InvariantCulture));
                // facing: the feet reach 0.2 m forward and 0.1 m back (README: the robot looks +Z in Unity)
                smr.BakeMesh(mesh, true);
                var mtx = smr.transform.localToWorldMatrix;
                float zMin = float.MaxValue, zMax = float.MinValue;
                foreach (var v in mesh.vertices)
                {
                    var w = mtx.MultiplyPoint3x4(v);
                    if (w.y > 0.12f) continue;
                    zMin = Mathf.Min(zMin, w.z);
                    zMax = Mathf.Max(zMax, w.z);
                }
                Check("fbx: robot looks +Z (feet reach forward)", zMax > -zMin,
                      "feet z " + zMin.ToString("0.000", CultureInfo.InvariantCulture) + " .. " + zMax.ToString("0.000", CultureInfo.InvariantCulture));
                // root motion: ClimbUp ends 1.20 m up and 0.36 m forward (+Z) on the ledge (README › Phase 6)
                var climb = p.Clips.FirstOrDefault(c => c.name == "ClimbUp");
                if (climb != null)
                {
                    climb.SampleAnimation(go, climb.length);
                    var root = go.GetComponentsInChildren<Transform>(true).First(t => t.name == "Root").position;
                    Check("clips: ClimbUp root ends 1.20 up / 0.36 forward (+Z)",
                          Mathf.Abs(root.y - 1.2f) < 0.02f && Mathf.Abs(root.z - 0.36f) < 0.02f && Mathf.Abs(root.x) < 0.02f,
                          root.ToString("F3"));
                }
                var below = new List<string>();
                foreach (var clip in p.Clips)
                {
                    float worst = 0f;
                    for (float t = 0f; t <= clip.length + 1e-4f; t += 4f / 30f)
                    {
                        clip.SampleAnimation(go, t);
                        worst = Mathf.Min(worst, MinY(out _));
                    }
                    if (worst < -0.01f) below.Add(clip.name + " " + worst.ToString("0.000", CultureInfo.InvariantCulture));
                }
                Check("pose: no clip below the floor (every 4th frame, 1 cm)", below.Count == 0, string.Join(",", below));
                UnityEngine.Object.DestroyImmediate(mesh);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        static void CheckLooks(Prefabs p)
        {
            var bad = new List<string>();
            foreach (var guid in AssetDatabase.FindAssets("", new[] { TestbedImport.ImportDir + "/Looks" }))
            {
                string path = AssetDatabase.GUIDToAssetPath(guid);
                if (!path.EndsWith(".json")) continue;
                var warnings = new List<string>();
                try
                {
                    RobotLook.Parse(File.ReadAllText(path), p.Config, warnings);
                    if (warnings.Count > 0) bad.Add(Path.GetFileName(path) + ": " + string.Join("; ", warnings));
                }
                catch (Exception e)
                {
                    bad.Add(Path.GetFileName(path) + ": " + e.Message);
                }
            }
            Check("looks: every type look reads without warnings", bad.Count == 0, string.Join(" | ", bad));
            var go = (GameObject)PrefabUtility.InstantiatePrefab(p.Modular);
            try
            {
                var app = go.GetComponent<RobotAppearance>();
                var skins = p.Config.Objects("skins").ToList();
                foreach (var s in skins)
                {
                    var look = new RobotLook();
                    look.ApplySkin(s, true);
                    app.Apply(look);
                }
                Check("looks: every skin applies (" + skins.Count + ")", true, skins.Count);
            }
            finally
            {
                UnityEngine.Object.DestroyImmediate(go);
            }
        }

        static void WriteReport(double seconds)
        {
            var sb = new StringBuilder();
            sb.Append("{\n  \"ok\": ").Append(Checks.All(c => c.ok) && Checks.Count > 0 ? "true" : "false");
            sb.Append(",\n  \"passed\": ").Append(Checks.Count(c => c.ok)).Append(",\n  \"total\": ").Append(Checks.Count);
            sb.Append(",\n  \"seconds\": ").Append(seconds.ToString("0.0", CultureInfo.InvariantCulture));
            sb.Append(",\n  \"unity\": \"").Append(Application.unityVersion).Append("\",\n  \"checks\": [\n");
            for (int i = 0; i < Checks.Count; i++)
            {
                var c = Checks[i];
                sb.Append("    {\"name\": \"").Append(Esc(c.name)).Append("\", \"ok\": ").Append(c.ok ? "true" : "false")
                  .Append(", \"detail\": \"").Append(Esc(c.detail)).Append("\"}").Append(i + 1 < Checks.Count ? ",\n" : "\n");
            }
            sb.Append("  ]\n}\n");
            File.WriteAllText(Path.Combine(ReportDir, "report.json"), sb.ToString());
        }

        static string Esc(string s) => s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", " ");
    }
}
