using System.Collections.Generic;
using UnityEngine;

namespace RobotTestbed
{
    /// <summary>Applies a RobotLook to the body/fabric materials (CubeEscape/RobotBody) of all renderers below it via
    /// MaterialPropertyBlocks: palette, finish, second colour zones, badges, glow lines. Values are linear, so they go
    /// in with SetVector (SetColor would convert from sRGB once more).</summary>
    public sealed class RobotAppearance : MonoBehaviour
    {
        [SerializeField] TextAsset config;
        public RobotConfig Config { get; private set; }
        public RobotLook Look { get; private set; } = new RobotLook();

        static readonly int Armor = Shader.PropertyToID("_Armor"), Armor2 = Shader.PropertyToID("_Armor2"),
            Mech = Shader.PropertyToID("_Mech"), Metal = Shader.PropertyToID("_Metal"), Lights = Shader.PropertyToID("_Lights"),
            Visor = Shader.PropertyToID("_Visor"), Fabric = Shader.PropertyToID("_FabricColor"), RefA = Shader.PropertyToID("_RefA"),
            RefB = Shader.PropertyToID("_RefB"), Finish = Shader.PropertyToID("_Finish"), Zone0 = Shader.PropertyToID("_ZoneSel0"),
            Zone1 = Shader.PropertyToID("_ZoneSel1"), LineSel0 = Shader.PropertyToID("_LineSel0"),
            LineSel1 = Shader.PropertyToID("_LineSel1"), LineColor = Shader.PropertyToID("_LineColor"),
            Glow = Shader.PropertyToID("_Glow");
        static readonly int[] BadgeSel = { Shader.PropertyToID("_BadgeA0"), Shader.PropertyToID("_BadgeA1"),
            Shader.PropertyToID("_BadgeB0"), Shader.PropertyToID("_BadgeB1") };
        static readonly int[] BadgeColor = { Shader.PropertyToID("_BadgeAColor"), Shader.PropertyToID("_BadgeBColor") };

        MaterialPropertyBlock block;
        readonly List<Material> shared = new List<Material>();
        readonly List<(Renderer renderer, int index)> bodySlots = new List<(Renderer, int)>();
        float power = 1f;

        public void Init(TextAsset configAsset)
        {
            config = configAsset;
            Config = null;
        }

        RobotConfig Cfg => Config ??= new RobotConfig(config.text);

        /// <summary>Emission factor of the clip ("power": Death 0, Respawn back to 1).</summary>
        /// <summary>Only _Glow changes: no renderer search, no allocation (the game calls this every frame).</summary>
        public void SetPower(float p)
        {
            power = p;
            if (bodySlots.Count == 0) Apply(Look);
            block ??= new MaterialPropertyBlock();
            float g = Mathf.Min(Look.Glow, 1.6f) * power;
            for (int s = 0; s < bodySlots.Count; s++)
            {
                var (r, i) = bodySlots[s];
                r.GetPropertyBlock(block, i);
                block.SetFloat(Glow, g);
                r.SetPropertyBlock(block, i);
            }
        }

        public void Apply(RobotLook look)
        {
            Look = look;
            var cfg = Cfg;
            var classic = cfg.Skin("Classic");
            var refA = new Vector4(RobotConfig.Luminance(RobotConfig.Vec(classic["armor"])),
                RobotConfig.Luminance(RobotConfig.Vec(classic["mech"])), RobotConfig.Luminance(RobotConfig.Vec(classic["metal"])),
                RobotConfig.Luminance(RobotConfig.Vec(classic["accent"])));
            var refB = new Vector4(RobotConfig.Luminance(RobotConfig.Vec(classic["visor"])), 0.71f, 0f, 0f);
            var zones = new Vector4[2];
            foreach (var z in look.Zones2)
            {
                var sel = cfg.Selector("zones", z);
                for (int t = 0; t < 2; t++) zones[t] += sel[t];
            }
            var lines = look.LinePattern == "None" ? new Vector4[2] : cfg.Selector("linePatterns", look.LinePattern);
            var finish = cfg.Finish(look.Finish);
            block ??= new MaterialPropertyBlock();
            bodySlots.Clear();
            foreach (var r in GetComponentsInChildren<Renderer>(true))
            {
                r.GetSharedMaterials(shared);
                for (int i = 0; i < shared.Count; i++)
                {
                    var m = shared[i];
                    if (m == null || m.shader.name != "CubeEscape/RobotBody") continue;
                    bodySlots.Add((r, i));
                    r.GetPropertyBlock(block, i);
                    block.SetVector(Armor, look.Colors["armor"]);
                    block.SetVector(Armor2, look.Armor2);
                    block.SetVector(Mech, look.Colors["mech"]);
                    block.SetVector(Metal, look.Colors["metal"]);
                    block.SetVector(Lights, look.Colors["lights"]);
                    block.SetVector(Visor, look.Colors["visor"]);
                    block.SetVector(Fabric, look.Colors["fabric"]);
                    block.SetVector(RefA, refA);
                    block.SetVector(RefB, refB);
                    block.SetVector(Finish, new Vector4(finish.x, finish.y, 0f, 0f));
                    block.SetVector(Zone0, zones[0]);
                    block.SetVector(Zone1, zones[1]);
                    for (int b = 0; b < 2; b++)
                    {
                        var sel = b < look.Badges.Count ? cfg.Selector("badges", look.Badges[b].id) : new Vector4[2];
                        block.SetVector(BadgeSel[2 * b], sel[0]);
                        block.SetVector(BadgeSel[2 * b + 1], sel[1]);
                        block.SetVector(BadgeColor[b], b < look.Badges.Count ? look.Badges[b].color : Vector4.zero);
                    }
                    block.SetVector(LineSel0, lines[0]);
                    block.SetVector(LineSel1, lines[1]);
                    var lc = look.LineColor;
                    block.SetVector(LineColor, new Vector4(lc.x, lc.y, lc.z, look.LineStrength));
                    block.SetFloat(Glow, Mathf.Min(look.Glow, 1.6f) * power);
                    r.SetPropertyBlock(block, i);
                }
            }
        }

        /// <summary>Shows only the parts of the look's variants (modular mesh: objects named Slot_Variant).</summary>
        public void ShowVariants()
        {
            foreach (Transform child in GetComponentsInChildren<Transform>(true))
            {
                if (!(child.GetComponent<Renderer>() is Renderer)) continue;
                string n = child.name;
                int us = n.IndexOf('_');
                if (n == "Robot_Body" || us < 0) continue;
                string slot = n.Substring(0, us), variant = n.Substring(us + 1);
                if (Look.Variants.TryGetValue(slot, out var want)) child.gameObject.SetActive(want == variant);
            }
        }
    }
}
