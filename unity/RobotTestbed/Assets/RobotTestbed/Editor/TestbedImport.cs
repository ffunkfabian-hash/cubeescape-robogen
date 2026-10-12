using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

namespace RobotTestbed.Editor
{
    /// <summary>Import settings for the generator output in Assets/RobotImport, exactly as README › Unity-Integration
    /// recommends: FBX scale 1 with bake axis conversion, Generic rig, clips with loop flags from robot_config.json and
    /// Bake Into Pose except the root motion clips; data textures linear and uncompressed, atlases without rescaling.</summary>
    public sealed class TestbedImport : AssetPostprocessor
    {
        public const string ImportDir = "Assets/RobotImport";
        public const string MainFbx = ImportDir + "/CubeEscape_Robot.fbx";
        // root motion kept (README): rotation for turns, position for ClimbUp
        public static readonly HashSet<string> RootRotation = new HashSet<string> { "TurnLeft", "TurnRight", "DanceSpin" };
        public static readonly HashSet<string> RootPosition = new HashSet<string> { "ClimbUp" };

        static bool Ours(string path) => path.StartsWith(ImportDir + "/");

        void OnPreprocessModel()
        {
            if (!Ours(assetPath)) return;
            var mi = (ModelImporter)assetImporter;
            mi.globalScale = 1f;
            mi.useFileUnits = true;
            mi.bakeAxisConversion = true;
            mi.importNormals = ModelImporterNormals.Import;
            mi.importTangents = ModelImporterTangents.CalculateMikk;
            mi.animationType = ModelImporterAnimationType.Generic;
            mi.importAnimation = assetPath == MainFbx;
            mi.materialImportMode = ModelImporterMaterialImportMode.ImportStandard;
            if (assetPath == MainFbx) mi.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
        }

        void OnPreprocessAnimation()
        {
            if (assetPath != MainFbx) return;
            var mi = (ModelImporter)assetImporter;
            string cfgPath = Path.Combine(Path.GetDirectoryName(Application.dataPath), ImportDir, "robot_config.json");
            if (!File.Exists(cfgPath)) return;
            var cfg = new RobotConfig(File.ReadAllText(cfgPath));
            var clips = mi.defaultClipAnimations;
            foreach (var c in clips)
            {
                string name = ClipName(c.takeName);
                c.name = name;
                var meta = cfg.Clip(name);
                c.loopTime = meta != null && (bool)meta["loop"];
                c.lockRootRotation = !RootRotation.Contains(name);
                c.lockRootHeightY = !RootPosition.Contains(name);
                c.lockRootPositionXZ = !RootPosition.Contains(name);
                c.keepOriginalOrientation = true;
                c.keepOriginalPositionY = true;
                c.keepOriginalPositionXZ = true;
            }
            mi.clipAnimations = clips;
        }

        /// <summary>Blender exports takes as "Armature|Clip" or "Clip"; the clip name is the part after '|'.</summary>
        public static string ClipName(string take)
        {
            int i = take.LastIndexOf('|');
            return i >= 0 ? take.Substring(i + 1) : take;
        }

        void OnPreprocessTexture()
        {
            if (!Ours(assetPath)) return;
            var ti = (TextureImporter)assetImporter;
            string n = Path.GetFileNameWithoutExtension(assetPath);
            ti.maxTextureSize = 4096;
            ti.npotScale = TextureImporterNPOTScale.None;      // the eye atlas is 2048 x 2560
            ti.alphaIsTransparency = false;                   // alpha is a mask channel, not transparency
            ti.mipmapEnabled = true;
            if (n == "T_Robot_Normal")
            {
                ti.textureType = TextureImporterType.NormalMap;
                return;
            }
            ti.textureType = TextureImporterType.Default;
            bool color = n.StartsWith("T_Robot_BaseColor") || n.StartsWith("T_Robot_Emission");
            ti.sRGBTexture = color;
            ti.textureCompression = color ? TextureImporterCompression.CompressedHQ : TextureImporterCompression.Uncompressed;
            if (n.Contains("Atlas") || n.Contains("Flipbook"))
            {
                ti.wrapMode = TextureWrapMode.Clamp;
                ti.mipmapEnabled = false;                     // cells would bleed into each other
            }
        }
    }
}
