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
            Glow = Shader.PropertyToID("_Glow"), MatSel0 = Shader.PropertyToID("_MaterialSel0"),
            MatSel1 = Shader.PropertyToID("_MaterialSel1"), MatColor = Shader.PropertyToID("_MaterialColor"),
            LayerAmount = Shader.PropertyToID("_LayerAmount"), LayerAmount2 = Shader.PropertyToID("_LayerAmount2"),
            LayerRough = Shader.PropertyToID("_LayerRough"), LayerMetal = Shader.PropertyToID("_LayerMetal"),
            Wear = Shader.PropertyToID("_Wear"), BareMetal = Shader.PropertyToID("_BareMetal");
        static readonly string[] LayerIds = { "Rust", "Moss", "Frost", "Embers", "Camo" };
        static readonly int[] LayerColor = { Shader.PropertyToID("_RustColor"), Shader.PropertyToID("_MossColor"),
            Shader.PropertyToID("_FrostColor"), Shader.PropertyToID("_EmberColor"), Shader.PropertyToID("_CamoColor") };
        static readonly int[] BadgeSel = { Shader.PropertyToID("_BadgeA0"), Shader.PropertyToID("_BadgeA1"),
            Shader.PropertyToID("_BadgeA2"), Shader.PropertyToID("_BadgeB0"), Shader.PropertyToID("_BadgeB1"),
            Shader.PropertyToID("_BadgeB2") };
        const int BadgeMasks = 3;
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
            Dictionary<string, object> material = null;
            foreach (var m in cfg.Objects("materials"))
                if ((string)m["id"] == look.Material) material = m;
            if (material != null && look.Finish == "Standard")      // the material brings its own surface
                finish = new Vector2((float)(double)material["roughness"], (float)(double)material["metalOffset"]);
            var matSel = material != null ? cfg.Selector("materials", look.Material) : new Vector4[2];
            var layerInfo = new Dictionary<string, Dictionary<string, object>>();
            foreach (var li in cfg.Objects("layers")) layerInfo[(string)li["id"]] = li;
            var amounts = new float[5];
            var colors = new Vector4[5];
            for (int k = 0; k < 5; k++)
            {
                colors[k] = layerInfo.TryGetValue(LayerIds[k], out var inf) && inf["color"] != null ? RobotConfig.Vec(inf["color"]) : Vector4.zero;
                foreach (var ly in look.Layers)
                    if (ly.id == LayerIds[k]) { amounts[k] = ly.amount; colors[k] = ly.color; }
            }
            var rough4 = Vector4.one;
            var metal4 = Vector4.zero;
            for (int k = 0; k < 4; k++)
                if (layerInfo.TryGetValue(LayerIds[k], out var inf2) && inf2["color"] != null)
                {
                    rough4[k] = (float)(double)inf2["roughness"];
                    metal4[k] = (float)(double)inf2["metalOffset"];
                }
            var weathering = cfg.Root.TryGetValue("weathering", out var wobj) ? (Dictionary<string, object>)wobj : null;
            float soft = weathering != null ? (float)(double)weathering["coverSoft"] : 0.1f;
            var bare = weathering != null ? RobotConfig.Vec(weathering["bareMetal"]) : new Vector4(0.62f, 0.63f, 0.65f);
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
                        var sel = b < look.Badges.Count ? cfg.Selector("badges", look.Badges[b].id) : new Vector4[BadgeMasks];
                        for (int t = 0; t < BadgeMasks; t++)
                            block.SetVector(BadgeSel[BadgeMasks * b + t], t < sel.Length ? sel[t] : Vector4.zero);
                        block.SetVector(BadgeColor[b], b < look.Badges.Count ? look.Badges[b].color : Vector4.zero);
                    }
                    block.SetVector(LineSel0, lines[0]);
                    block.SetVector(LineSel1, lines[1]);
                    var lc = look.LineColor;
                    block.SetVector(LineColor, new Vector4(lc.x, lc.y, lc.z, look.LineStrength));
                    block.SetFloat(Glow, Mathf.Min(look.Glow, 1.6f) * power);
                    block.SetVector(MatSel0, matSel[0]);
                    block.SetVector(MatSel1, matSel[1]);
                    var mc = look.MaterialColor;
                    block.SetVector(MatColor, new Vector4(mc.x, mc.y, mc.z, material != null ? 1f : 0f));
                    block.SetVector(LayerAmount, new Vector4(amounts[0], amounts[1], amounts[2], amounts[3]));
                    block.SetVector(LayerAmount2, new Vector4(amounts[4], soft, 0f, 0f));
                    for (int k = 0; k < 5; k++) block.SetVector(LayerColor[k], colors[k]);
                    block.SetVector(LayerRough, rough4);
                    block.SetVector(LayerMetal, metal4);
                    block.SetFloat(Wear, look.Wear);
                    block.SetVector(BareMetal, bare);
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
