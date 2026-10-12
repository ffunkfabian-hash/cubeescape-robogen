using System.Collections.Generic;
using UnityEngine;

namespace RobotTestbed
{
    /// <summary>robot_config.json of the generator: skins, mask channels, atlas cells, clip data.</summary>
    public sealed class RobotConfig
    {
        public readonly Dictionary<string, object> Root;

        public RobotConfig(string json)
        {
            Root = (Dictionary<string, object>)Json.Parse(json);
        }

        public List<object> List(string key) => Root.TryGetValue(key, out var v) && v is List<object> l ? l : new List<object>();

        public IEnumerable<Dictionary<string, object>> Objects(string key)
        {
            foreach (var o in List(key)) yield return (Dictionary<string, object>)o;
        }

        public Dictionary<string, object> Skin(string id)
        {
            foreach (var s in Objects("skins"))
                if ((string)s["name"] == id) return s;
            return null;
        }

        public List<string> Strings(string key)
        {
            var r = new List<string>();
            foreach (var o in List(key)) r.Add((string)o);
            return r;
        }

        /// <summary>Static display motifs and animated ones (flipbook).</summary>
        public List<string> AllDisplays()
        {
            var r = Strings("displays");
            foreach (var a in Objects("displayAnimations")) r.Add((string)a["name"]);
            return r;
        }

        /// <summary>Mask texture file names of a channel list (badges, linePatterns, zones) in first-use order.</summary>
        public List<string> MaskTextures(string key)
        {
            var r = new List<string>();
            foreach (var c in Objects(key))
            {
                string t = (string)c["texture"];
                if (!r.Contains(t)) r.Add(t);
            }
            return r;
        }

        /// <summary>Channel selector of an ID: one Vector4 per mask texture of the list, 1 in the ID's channel.</summary>
        public Vector4[] Selector(string key, string id)
        {
            var textures = MaskTextures(key);
            var sel = new Vector4[Mathf.Max(2, textures.Count)];
            foreach (var c in Objects(key))
            {
                if ((string)c["id"] != id) continue;
                int t = textures.IndexOf((string)c["texture"]);
                int ch = (int)(double)c["channel"];
                Vector4 v = sel[t];
                v[ch] = 1f;
                sel[t] = v;
            }
            return sel;
        }

        public Vector2 Finish(string id)
        {
            foreach (var f in Objects("finishes"))
                if ((string)f["id"] == id) return new Vector2((float)(double)f["roughness"], (float)(double)f["metalOffset"]);
            return new Vector2(1f, 0f);
        }

        /// <summary>Atlas cell (scale u, scale v, offset u, offset v) for eyes, displays or displayAnimations.</summary>
        public bool AtlasCell(string kind, string name, out Vector4 cell)
        {
            cell = Vector4.zero;
            if (!(Root["atlasCells"] is Dictionary<string, object> cells) || !cells.TryGetValue(kind, out var k) ||
                !((Dictionary<string, object>)k).TryGetValue(name, out var c)) return false;
            var l = (List<object>)c;
            cell = new Vector4((float)(double)l[0], (float)(double)l[1], (float)(double)l[2], (float)(double)l[3]);
            return true;
        }

        public Dictionary<string, object> Clip(string name)
        {
            return Root.TryGetValue("clips", out var c) && ((Dictionary<string, object>)c).TryGetValue(name, out var v)
                ? (Dictionary<string, object>)v : null;
        }

        public static Vector4 Vec(object rgbList, float w = 0f)
        {
            var l = (List<object>)rgbList;
            return new Vector4((float)(double)l[0], (float)(double)l[1], (float)(double)l[2], w);
        }

        public static float Luminance(Vector4 c) => 0.2126f * c.x + 0.7152f * c.y + 0.0722f * c.z;
    }
}
