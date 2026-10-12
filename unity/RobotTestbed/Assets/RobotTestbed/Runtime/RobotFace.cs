using System.Collections.Generic;
using UnityEngine;

namespace RobotTestbed
{
    /// <summary>Eyes and chest display (CubeEscape/RobotScreen): atlas cell per expression/motif, expression styles
    /// (tint, glow), animated motifs from the flipbook and idle blinking - all as data from robot_config.json.</summary>
    public sealed class RobotFace : MonoBehaviour
    {
        [SerializeField] TextAsset configAsset;
        [SerializeField] Texture displayAtlas;
        [SerializeField] Texture flipbookAtlas;

        static readonly int Cell = Shader.PropertyToID("_Cell"), Tint = Shader.PropertyToID("_Tint"),
            Glow = Shader.PropertyToID("_Glow"), Atlas = Shader.PropertyToID("_Atlas");

        RobotConfig parsed;
        RobotConfig config => parsed ??= configAsset != null ? new RobotConfig(configAsset.text) : null;
        MaterialPropertyBlock block;
        readonly List<Material> shared = new List<Material>();
        string expression = "Normal", display = "Status";
        Vector4 eyeColor = new Vector4(0.02f, 0.62f, 0.95f), screenColor = new Vector4(0.3f, 1f, 0.62f);
        float glow = 1f, power = 1f, nextBlink, blinkUntil;
        bool blinkShown;
        int animRow = -1, animFrames, shownFrame = -1;
        float animFps;
        Vector4 animCell;

        public void Init(TextAsset cfg, Texture staticAtlas, Texture flipbook)
        {
            configAsset = cfg;
            parsed = null;
            displayAtlas = staticAtlas;
            flipbookAtlas = flipbook;
        }

        public void Apply(RobotLook look)
        {
            eyeColor = look.Colors["eyes"];
            screenColor = look.Colors["screen"];
            glow = look.Glow;
            SetExpression(look.Expression);
            SetDisplay(look.Display);
        }

        public void SetPower(float p)
        {
            power = p;
            SetExpression(expression);
            SetDisplay(display, true);
        }

        public void SetExpression(string name, bool blinking = false)
        {
            expression = name;
            string shown = name;
            if (blinking && config.Root.TryGetValue("blink", out var b))
                shown = (string)((Dictionary<string, object>)b)["expression"];
            if (!config.AtlasCell("eyes", shown, out var cell)) return;
            Vector4 tint = eyeColor;
            float g = 1f;
            if (config.Root.TryGetValue("expressionStyles", out var s) &&
                ((Dictionary<string, object>)s).TryGetValue(shown, out var st))
            {
                var style = (Dictionary<string, object>)st;
                if (style.TryGetValue("tint", out var t)) tint = RobotConfig.Vec(t);
                if (style.TryGetValue("glow", out var gl)) g = (float)(double)gl;
            }
            Set("CubeEscape/RobotScreen", 0, cell, tint, glow * power * g, null);
        }

        public void SetDisplay(string name, bool force = false)
        {
            if (name == display && !force && animRow < 0 && shownFrame >= 0) return;
            display = name;
            animRow = -1;
            foreach (var a in config.Objects("displayAnimations"))
            {
                if ((string)a["name"] != name) continue;
                animRow = (int)(double)a["row"];
                animFrames = (int)(double)a["frames"];
                animFps = (float)(double)a["fps"];
                config.AtlasCell("displayAnimations", name, out animCell);
                SetFlipbookFrame(0);
                return;
            }
            if (config.AtlasCell("displays", name, out var cell))
                Set("CubeEscape/RobotScreen", 1, cell, screenColor, Mathf.Min(power, 1.5f), displayAtlas);
            shownFrame = 0;
        }

        void SetFlipbookFrame(int frame)
        {
            shownFrame = frame;
            var cell = new Vector4(animCell.x, animCell.y, animCell.z + frame * animCell.x, animCell.w);
            Set("CubeEscape/RobotScreen", 1, cell, screenColor, Mathf.Min(power, 1.5f), flipbookAtlas);
        }

        /// <summary>Sets the atlas cell on the screen materials of one mode (0 eyes, 1 display).</summary>
        void Set(string shader, float mode, Vector4 cell, Vector4 tint, float g, Texture atlas)
        {
            block ??= new MaterialPropertyBlock();
            foreach (var r in GetComponentsInChildren<Renderer>(true))
            {
                r.GetSharedMaterials(shared);
                for (int i = 0; i < shared.Count; i++)
                {
                    var m = shared[i];
                    if (m == null || m.shader.name != shader || Mathf.Abs(m.GetFloat("_Mode") - mode) > 0.5f) continue;
                    r.GetPropertyBlock(block, i);
                    block.SetVector(Cell, cell);
                    block.SetVector(Tint, tint);
                    block.SetFloat(Glow, g);
                    if (atlas != null) block.SetTexture(Atlas, atlas);
                    r.SetPropertyBlock(block, i);
                }
            }
        }

        /// <summary>Flipbook frames and idle blinking (only over the expressions the blink rule names).</summary>
        public void Tick(float time)
        {
            if (animRow >= 0)
            {
                int f = (int)(time * animFps) % Mathf.Max(animFrames, 1);
                if (f != shownFrame) SetFlipbookFrame(f);
            }
            if (!config.Root.TryGetValue("blink", out var b)) return;
            var blink = (Dictionary<string, object>)b;
            var interval = (List<object>)blink["interval"];
            if (nextBlink <= 0f) nextBlink = time + (float)(double)interval[0];
            if (time >= nextBlink)
            {
                blinkUntil = time + (float)(double)blink["duration"];
                nextBlink = time + Random.Range((float)(double)interval[0], (float)(double)interval[1]);
            }
            bool during = ((List<object>)blink["during"]).Contains(expression);
            SetExpression(expression, during && time < blinkUntil);
        }

        void Update()
        {
            if (config != null) Tick(Time.time);
        }
    }
}
