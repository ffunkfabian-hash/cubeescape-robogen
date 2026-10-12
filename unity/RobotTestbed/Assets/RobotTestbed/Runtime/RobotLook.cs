using System;
using System.Collections.Generic;
using UnityEngine;

namespace RobotTestbed
{
    /// <summary>A player look (*.robotlook.json, format CubeEscapeRobotLook version 3) with the same migration and
    /// sanitising rules as the viewer (viewer/Sources/Look.swift): version 1 and 2 are migrated, unknown IDs fall back
    /// to defaults with a warning, newer versions are rejected. Colours are linear RGB in x, y, z.</summary>
    public sealed class RobotLook
    {
        public const string Format = "CubeEscapeRobotLook";
        public const int CurrentVersion = 4;
        public const float WearDefault = 0.5f;
        public const int MaxBadges = 2;
        static readonly string[] ColorKeys = { "armor", "mech", "metal", "lights", "screen", "eyes", "visor", "fabric" };

        public string Name = "Classic";
        public Dictionary<string, string> Variants = new Dictionary<string, string>();
        public Dictionary<string, Vector4> Colors = new Dictionary<string, Vector4>
        {
            { "armor", new Vector4(0.66f, 0.685f, 0.72f) }, { "mech", new Vector4(0.075f, 0.08f, 0.092f) },
            { "metal", new Vector4(0.38f, 0.395f, 0.42f) }, { "lights", new Vector4(1f, 0.38f, 0.05f) },
            { "screen", new Vector4(0.3f, 1f, 0.62f) }, { "eyes", new Vector4(0.02f, 0.62f, 0.95f) },
            { "visor", new Vector4(0.006f, 0.007f, 0.01f) }, { "fabric", new Vector4(0.78f, 0.1f, 0.12f) },
        };
        public string Finish = "Standard";
        public float Glow = 1f;
        public string Expression = "Normal";
        public string Display = "Status";
        public bool AutoExpression = true;
        public bool AutoDisplay = true;
        public string Skin = "Classic";
        public Vector4 Armor2 = new Vector4(0.66f, 0.685f, 0.72f);
        public List<string> Zones2 = new List<string>();
        public List<(string id, Vector4 color)> Badges = new List<(string, Vector4)>();
        public string LinePattern = "None";
        public Vector4 LineColor = new Vector4(1f, 0.38f, 0.05f);
        public float LineStrength = 1f;
        public string Material = "None";
        public Vector4 MaterialColor = new Vector4(0.3f, 0.2f, 0.1f);
        public List<(string id, float amount, Vector4 color)> Layers = new List<(string, float, Vector4)>();
        public float Wear = WearDefault;

        static Vector4 Rgb(object o)
        {
            var d = (Dictionary<string, object>)o;
            return new Vector4((float)(double)d["r"], (float)(double)d["g"], (float)(double)d["b"]);
        }

        static Vector4 Clamp01(Vector4 c) => new Vector4(Mathf.Clamp01(c.x), Mathf.Clamp01(c.y), Mathf.Clamp01(c.z));

        /// <summary>Reads, migrates and sanitises a look; warnings name every replaced value.</summary>
        public static RobotLook Parse(string json, RobotConfig config, List<string> warnings)
        {
            var d = (Dictionary<string, object>)Json.Parse(json);
            if (d.TryGetValue("format", out var f) && (string)f != Format)
                throw new FormatException("kein Roboter-Look: " + f);
            int version = d.TryGetValue("version", out var v) ? (int)(double)v : 1;
            if (version > CurrentVersion) throw new FormatException("Look-Version " + version + " ist neuer als dieses Testbett");
            var l = new RobotLook();
            if (d.TryGetValue("name", out var n)) l.Name = (string)n;
            foreach (var k in ColorKeys)
                if (d.TryGetValue(k, out var c)) l.Colors[k] = Rgb(c);
            if (d.TryGetValue("finish", out var fin)) l.Finish = (string)fin;
            if (d.TryGetValue("glow", out var g)) l.Glow = (float)(double)g;
            if (d.TryGetValue("expression", out var e)) l.Expression = (string)e;
            if (d.TryGetValue("display", out var ds)) l.Display = (string)ds;
            if (d.TryGetValue("autoExpression", out var ae)) l.AutoExpression = (bool)ae;
            if (d.TryGetValue("autoDisplay", out var ad)) l.AutoDisplay = (bool)ad;
            // version 3: before, a look was only colours - it keeps them and no longer claims a skin
            l.Skin = d.TryGetValue("skin", out var s) ? (string)s : (version < 3 ? "Custom" : "Classic");
            l.Armor2 = d.TryGetValue("armor2", out var a2) ? Rgb(a2) : l.Colors["armor"];
            if (d.TryGetValue("zones2", out var z))
                foreach (var o in (List<object>)z) l.Zones2.Add((string)o);
            if (d.TryGetValue("badges", out var b))
                foreach (var o in (List<object>)b)
                {
                    var bd = (Dictionary<string, object>)o;
                    l.Badges.Add(((string)bd["id"], Rgb(bd["color"])));
                }
            if (d.TryGetValue("lines", out var ln))
            {
                var ld = (Dictionary<string, object>)ln;
                l.LinePattern = (string)ld["pattern"];
                if (ld.TryGetValue("color", out var lc)) l.LineColor = Rgb(lc);
                if (ld.TryGetValue("strength", out var st)) l.LineStrength = (float)(double)st;
            }
            // version 4: no material, no layers, the baked wear
            if (d.TryGetValue("material", out var mt))
            {
                var md = (Dictionary<string, object>)mt;
                l.Material = (string)md["id"];
                if (md.TryGetValue("color", out var mc)) l.MaterialColor = Rgb(mc);
            }
            if (d.TryGetValue("layers", out var ls))
                foreach (var o in (List<object>)ls)
                {
                    var ld2 = (Dictionary<string, object>)o;
                    l.Layers.Add(((string)ld2["id"], (float)(double)ld2["amount"],
                                  ld2.TryGetValue("color", out var lc2) ? Rgb(lc2) : Vector4.zero));
                }
            if (d.TryGetValue("wear", out var wr)) l.Wear = (float)(double)wr;
            var variants = d.TryGetValue("variants", out var vv) ? (Dictionary<string, object>)vv : new Dictionary<string, object>();
            l.Sanitize(config, variants, warnings);
            return l;
        }

        void Sanitize(RobotConfig config, Dictionary<string, object> variants, List<string> warnings)
        {
            var slotIds = new HashSet<string>();
            foreach (var slot in config.Objects("slots"))
            {
                string id = (string)slot["id"];
                slotIds.Add(id);
                var allowed = (List<object>)slot["variants"];
                string fallback = allowed.Count > 0 ? (string)allowed[0] : "None";
                string want = variants.TryGetValue(id, out var x) ? (string)x : fallback;
                if (allowed.Contains(want)) Variants[id] = want;
                else { Variants[id] = fallback; warnings.Add("Bauteil " + id + ": " + want + " unbekannt"); }
            }
            foreach (var k in variants.Keys)
                if (!slotIds.Contains(k)) warnings.Add("Bauteil-Slot " + k + " unbekannt");
            if (!config.Strings("expressions").Contains(Expression)) { warnings.Add("Augen " + Expression + " unbekannt"); Expression = "Normal"; }
            if (!config.AllDisplays().Contains(Display)) { warnings.Add("Display " + Display + " unbekannt"); Display = "Status"; }
            if (Skin != "Custom" && config.Skin(Skin) == null) { warnings.Add("Skin " + Skin + " unbekannt"); Skin = "Custom"; }
            var zoneIds = new HashSet<string>();
            foreach (var zc in config.Objects("zones")) zoneIds.Add((string)zc["id"]);
            var zones = new List<string>();
            foreach (var zid in Zones2)
            {
                if (!zoneIds.Contains(zid)) warnings.Add("Lackzone " + zid + " unbekannt");
                else if (!zones.Contains(zid)) zones.Add(zid);
            }
            Zones2 = zones;
            var badgeIds = new HashSet<string>();
            foreach (var bc in config.Objects("badges")) badgeIds.Add((string)bc["id"]);
            var badges = new List<(string, Vector4)>();
            foreach (var bl in Badges)
            {
                if (!badgeIds.Contains(bl.id)) warnings.Add("Abzeichen " + bl.id + " unbekannt");
                else badges.Add((bl.id, Clamp01(bl.color)));
            }
            if (badges.Count > MaxBadges) warnings.Add("mehr als " + MaxBadges + " Abzeichen – überzählige entfernt");
            Badges = badges.Count > MaxBadges ? badges.GetRange(0, MaxBadges) : badges;
            bool knownLine = false;
            foreach (var lp in config.Objects("linePatterns")) knownLine |= (string)lp["id"] == LinePattern;
            if (LinePattern != "None" && !knownLine) { warnings.Add("Leuchtlinien " + LinePattern + " unbekannt"); LinePattern = "None"; }
            LineStrength = Mathf.Clamp(LineStrength, 0f, 2f);
            bool knownMaterial = false;
            foreach (var m in config.Objects("materials")) knownMaterial |= (string)m["id"] == Material;
            if (Material != "None" && !knownMaterial) { warnings.Add("Werkstoff " + Material + " unbekannt"); Material = "None"; }
            var layerIds = new HashSet<string>();
            foreach (var lc in config.Objects("layers"))
                if (lc.ContainsKey("color") && lc["color"] != null) layerIds.Add((string)lc["id"]);
            var kept = new List<(string, float, Vector4)>();
            var seen = new HashSet<string>();
            foreach (var ly in Layers)
            {
                if (!layerIds.Contains(ly.id)) { warnings.Add("Schicht " + ly.id + " unbekannt"); continue; }
                if (seen.Add(ly.id)) kept.Add((ly.id, Mathf.Clamp01(ly.amount), Clamp01(ly.color)));
            }
            Layers = kept;
            Wear = Mathf.Clamp01(Wear);
            MaterialColor = Clamp01(MaterialColor);
            Glow = Mathf.Clamp(Glow, 0.2f, 2.5f);
            foreach (var k in ColorKeys) Colors[k] = Clamp01(Colors[k]);
            Armor2 = Clamp01(Armor2);
            LineColor = Clamp01(LineColor);
        }

        /// <summary>Takes everything a skin defines; with parts also its variants, expression and display.</summary>
        public void ApplySkin(Dictionary<string, object> skin, bool parts = false)
        {
            var keys = new Dictionary<string, string>
            {
                { "armor", "armor" }, { "mech", "mech" }, { "metal", "metal" }, { "lights", "accent" },
                { "screen", "screen" }, { "eyes", "eye" }, { "visor", "visor" }, { "fabric", "fabric" },
            };
            foreach (var kv in keys) Colors[kv.Key] = RobotConfig.Vec(skin[kv.Value]);
            Skin = (string)skin["name"];
            Armor2 = skin.TryGetValue("armor2", out var a2) ? RobotConfig.Vec(a2) : Colors["armor"];
            Zones2 = new List<string>();
            if (skin.TryGetValue("zones2", out var z)) foreach (var o in (List<object>)z) Zones2.Add((string)o);
            Finish = skin.TryGetValue("finish", out var f) ? (string)f : "Standard";
            Badges = new List<(string, Vector4)>();
            if (skin.TryGetValue("badges", out var b))
                foreach (var o in (List<object>)b)
                {
                    var bd = (Dictionary<string, object>)o;
                    Badges.Add(((string)bd["id"], RobotConfig.Vec(bd["color"])));
                }
            if (skin.TryGetValue("lines", out var ln) && ln is Dictionary<string, object> ld)
            {
                LinePattern = (string)ld["pattern"];
                LineColor = RobotConfig.Vec(ld["color"]);
                LineStrength = (float)(double)ld["strength"];
            }
            else
            {
                LinePattern = "None";
                LineColor = Colors["lights"];
                LineStrength = 1f;
            }
            Material = "None";
            if (skin.TryGetValue("material", out var mt) && mt is Dictionary<string, object> md)
            {
                Material = (string)md["id"];
                MaterialColor = RobotConfig.Vec(md["color"]);
            }
            Layers = new List<(string, float, Vector4)>();
            if (skin.TryGetValue("layers", out var ls))
                foreach (var o in (List<object>)ls)
                {
                    var layer = (Dictionary<string, object>)o;
                    Layers.Add(((string)layer["id"], (float)(double)layer["amount"], RobotConfig.Vec(layer["color"])));
                }
            Wear = skin.TryGetValue("wear", out var w) ? (float)(double)w : WearDefault;
            if (!parts) return;
            if (skin.TryGetValue("parts", out var p))
                foreach (var kv in (Dictionary<string, object>)p) Variants[kv.Key] = (string)kv.Value;
            if (skin.TryGetValue("expression", out var e)) Expression = (string)e;
            if (skin.TryGetValue("display", out var ds)) Display = (string)ds;
        }
    }
}
