import AppKit
import ImageIO
import Metal
import SceneKit
import simd

/// Builds the SceneKit scene from RobotData and applies looks and animation frames.
final class RobotScene: NSObject, SCNSceneRendererDelegate {
    let data: RobotData
    let scene = SCNScene()
    let robotRoot = SCNNode()
    let cameraNode = SCNNode()
    private(set) var boneNodes: [String: SCNNode] = [:]
    private var boneOrder: [SCNNode] = []
    private var objectNodes: [String: [SCNNode]] = [:]
    private var spinNodes: [SCNNode] = []
    private var bodyMat = SCNMaterial()
    private var fabricMat = SCNMaterial()
    private var eyeMat = SCNMaterial()
    private var displayMat = SCNMaterial()
    private var floor = SCNNode()
    private let classic: Skin

    // animation state (read on the render thread)
    private let lock = NSLock()
    private var clip: AnimInfo?
    private var playing = true
    private var speed: Double = 1.0
    private var loopClip = true
    private var clipTime: Double = 0
    private var lastTime: TimeInterval?
    private var turntable = false
    private var look = Look()
    private var shownExpression = ""
    private var wantedExpression = "Normal"
    private var shownDisplay = ""
    private var displayAnim: DisplayAnimation?     // animated motif (flipbook) currently shown
    private var displayFrame = -1
    private var blinkUntil: TimeInterval = 0
    private var nextBlink: TimeInterval = 0
    private var blinkClock: TimeInterval = 0
    private var eyeGlowStyle = 1.0
    private var lastPower = 1.0
    private var lastGlow = 1.0
    /// Packed mask textures per ViewerData folder: every RobotScene (A/B, exports) reuses them.
    private static var packCache: [String: Any] = [:]
    private var staticDisplayAtlas: NSImage?
    private var flipbookAtlas: NSImage?
    private var heldItemNodes: [SCNNode] = []
    private var heldItemVisible = false
    private var shownPower = -1.0
    private let propRoot = SCNNode()
    private var propClip = ""
    private var propsWanted = true
    var onFrame: ((Double) -> Void)?

    init(data: RobotData) {
        self.data = data
        classic = data.config.skins.first { $0.name == "Classic" } ?? data.config.skins[0]
        super.init()
        buildMaterials()
        buildRig()
        buildEnvironment()
    }

    // MARK: - scene

    private func image(_ name: String) -> NSImage? { NSImage(contentsOf: data.texture(name)) }


    /// Pixels of a texture as straight (not premultiplied) RGBA8, row 0 = top. NSImage premultiplies RGB by alpha,
    /// which wipes mask channels whenever the alpha channel holds another mask, so RGBA PNGs are read raw.
    private func pixels(_ name: String) -> (Int, Int, [UInt8])? {
        guard let src = CGImageSourceCreateWithURL(data.texture(name) as CFURL, nil),
              let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return nil }
        let w = cg.width, h = cg.height
        if cg.bitsPerComponent == 8, cg.bitsPerPixel == 32, cg.alphaInfo == .last || cg.alphaInfo == .noneSkipLast,
           let d = cg.dataProvider?.data, let ptr = CFDataGetBytePtr(d) {
            var out = [UInt8](repeating: 0, count: w * h * 4)
            for y in 0..<h { for x in 0..<(w * 4) { out[y * w * 4 + x] = ptr[y * cg.bytesPerRow + x] } }
            return (w, h, out)
        }
        var out = [UInt8](repeating: 0, count: w * h * 4)       // no alpha: drawing is lossless
        guard let ctx = CGContext(data: &out, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        return (w, h, out)
    }

    private static let device = MTLCreateSystemDefaultDevice()

    /// RGBA8 texture whose channels are picked from other textures: [(file, channel)] per output channel
    /// (nil = 0). SceneKit draws a shader modifier with more than six custom textures magenta, so the viewer
    /// packs the generator's masks into fewer textures. Zone masks become one zone number (index / 8).
    private func packed(_ channels: [(String, Int)?], zoneIDs: [String]? = nil, zoneChannel: Int = -1,
                        keepPixels: Bool = false) -> Any? {
        guard let (w, h, bytes) = packBytes(channels, zoneIDs: zoneIDs, zoneChannel: zoneChannel,
                                            keepPixels: keepPixels) else { return nil }
        return RobotScene.texture(w, h, bytes)
    }

    /// keepPixels: decoded files stay in packCache (masks that are repacked on every look change, e.g. badges).
    private func packBytes(_ channels: [(String, Int)?], zoneIDs: [String]? = nil,
                           zoneChannel: Int = -1, keepPixels: Bool = false) -> (Int, Int, [UInt8])? {
        var cache: [String: (Int, Int, [UInt8])] = [:]
        func px(_ f: String) -> (Int, Int, [UInt8])? {
            if let c = cache[f] { return c }
            let key = data.root.path + "#px:" + f
            if keepPixels, let c = RobotScene.packCache[key] as? (Int, Int, [UInt8]) { return c }
            let p = pixels(f)
            cache[f] = p
            if keepPixels, let p = p { RobotScene.packCache[key] = p }
            return p
        }
        let files = channels.compactMap { $0?.0 } + (zoneIDs ?? [])
        guard let size = files.lazy.compactMap({ px($0) }).first else { return nil }
        let (w, h) = (size.0, size.1)
        var out = [UInt8](repeating: 0, count: w * h * 4)
        for (o, ch) in channels.enumerated() {
            guard let (f, c) = ch, let p = px(f), p.0 == w, p.1 == h else { continue }
            for i in 0..<(w * h) { out[i * 4 + o] = p.2[i * 4 + c] }
        }
        if let zones = zoneIDs, zoneChannel >= 0 {       // zones: [file#channel] in zone order
            for (z, spec) in zones.enumerated() {
                let parts = spec.split(separator: "#")
                guard parts.count == 2, let c = Int(parts[1]), let p = px(String(parts[0])), p.0 == w else { continue }
                let v = UInt8((z + 1) * 255 / 8)
                for i in 0..<(w * h) where p.2[i * 4 + c] > 127 { out[i * 4 + zoneChannel] = v }
            }
        }
        return (w, h, out)
    }

    static func texture(_ w: Int, _ h: Int, _ bytes: [UInt8]) -> MTLTexture? {
        let desc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .rgba8Unorm, width: w, height: h,
                                                            mipmapped: false)
        desc.usage = .shaderRead
        guard let tex = device?.makeTexture(descriptor: desc) else { return nil }
        tex.replace(region: MTLRegionMake2D(0, 0, w, h), mipmapLevel: 0, withBytes: bytes, bytesPerRow: w * 4)
        return tex
    }

    private func buildMaterials() {
        bodyMat = makeBodyMaterial(fabric: false)
        fabricMat = makeBodyMaterial(fabric: true)

        eyeMat.lightingModel = .constant
        eyeMat.diffuse.contents = image("T_Robot_EyeAtlas.png")
        eyeMat.blendMode = .add
        eyeMat.writesToDepthBuffer = false
        eyeMat.isDoubleSided = false
        eyeMat.shaderModifiers = [.surface: """
        #pragma arguments
        texture2d<float> atlas;
        float4 cell;
        float4 eyeColor;
        float glow;
        #pragma body
        constexpr sampler s(filter::linear, address::clamp_to_zero);
        float l = atlas.sample(s, _surface.diffuseTexcoord * cell.xy + cell.zw).r;
        float3 c = eyeColor.rgb * l * glow + pow(l, 6.0) * 0.8 * min(glow, 1.5);
        _surface.diffuse = float4(c, 1.0);
        """]
        eyeMat.setValue(SCNMaterialProperty(contents: image("T_Robot_EyeAtlas.png") as Any), forKey: "atlas")

        displayMat.lightingModel = .constant
        displayMat.diffuse.contents = image("T_Robot_DisplayAtlas.png")
        displayMat.shaderModifiers = [.surface: """
        #pragma arguments
        texture2d<float> atlas;
        float4 cell;
        float4 screenColor;
        float power;
        #pragma body
        constexpr sampler s(filter::linear, address::clamp_to_edge);
        float l = atlas.sample(s, _surface.diffuseTexcoord * cell.xy + cell.zw).r;
        _surface.diffuse = float4(mix(float3(0.01, 0.025, 0.02), screenColor.rgb * 1.6 * power, l), 1.0);
        """]
        staticDisplayAtlas = image("T_Robot_DisplayAtlas.png")
        flipbookAtlas = data.config.flipbook.flatMap { image($0.texture) }
        displayMat.setValue(SCNMaterialProperty(contents: staticDisplayAtlas as Any), forKey: "atlas")
        displayMat.setValue(NSNumber(value: 1.0), forKey: "power")
    }

    private func makeBodyMaterial(fabric: Bool) -> SCNMaterial {
        let m = SCNMaterial()
        let args = CommandLine.arguments
        m.lightingModel = .physicallyBased
        m.diffuse.contents = image("T_Robot_BaseColor.png")
        if !args.contains("--debug-no-normal") { m.normal.contents = image("T_Robot_Normal.png") }
        m.emission.contents = image("T_Robot_Emission.png")
        if !args.contains("--debug-no-ao") { m.ambientOcclusion.contents = image("T_Robot_Occlusion.png") }
        m.metalness.contents = 0.5
        m.roughness.contents = 0.5
        for p in [m.diffuse, m.normal, m.emission, m.ambientOcclusion] {
            p.mipFilter = .linear
            p.wrapS = .repeat
            p.wrapT = .repeat
        }
        let body = fabric ? """
        c = P(5).rgb * (lum / max(refB.y, 1e-4));
        rough = 0.75; metalness = 0.0;
        """ : """
        float vis = saturate(1.0 - (mk.r + mk.g + mk.b + mk.a));
        c = mix(c, P(0).rgb * (lum / refA.x), mk.r);
        c = mix(c, P(1).rgb * (lum / refA.y), mk.g);
        c = mix(c, P(2).rgb * (lum / refA.z), mk.b);
        c = mix(c, P(3).rgb * (lum / refA.w), mk.a);
        c = mix(c, P(4).rgb * (lum / refB.x), vis);
        float4 finish = P(9);
        rough = mix(rough, saturate(rough * finish.x), mk.r);
        metalness = mix(metalness, saturate(metalness + finish.y), mk.r);
        rough = mix(rough, mix(rough, 0.04, 0.8), vis);
        float ratio = clamp(lum / max(mk.r * refA.x + mk.b * refA.z, 1e-3), 0.2, 1.6);
        float ratioAll = clamp(lum / max(mk.r * refA.x + mk.g * refA.y + mk.b * refA.z, 1e-3), 0.2, 1.6);
        int zid = int(round(aux.g * 8.0));
        float mz = zid >= 1 && zid <= 8 ? float((int(finish.w) >> (zid - 1)) & 1) : 0.0f;
        c = mix(c, P(6).rgb * ratio, mz);
        float3 region = mix(mk.r * P(0).rgb + mk.g * P(1).rgb + mk.b * P(2).rgb, P(6).rgb, mz);
        float4 misc = P(18);                      // line paint, glow, wear, material on
        float4 lay = layerTex.sample(s, uv);
        float wearF = ms.g, grime = ms.b;
        float4 amt = P(21), amt2 = P(22);         // rust, moss, frost, embers / camo, soft
        float w = misc.z;                         // wear before material and layers (cleaning keeps their patterns)
        if (w < 0.5) {
            float clean = (0.5 - w) * 2.0;
            c = mix(c, region, saturate(wearF * 1.6) * clean);
            c = mix(c, region, grime * clean * 0.4);
        } else {
            float more = (w - 0.5) * 2.0;
            c = mix(c, P(31).rgb, saturate(wearF * 4.0 * more) * 0.85);
            c *= 1.0 - 0.6 * more * grime;
        }
        if (misc.w > 0.5) c = mix(c, mix(P(19).rgb * ratio, c, aux.b), mk.r);
        float4 bd = badgeTex.sample(s, uv);       // R = first badge, G = second (packed per look)
        c = mix(c, P(14).rgb * ratio, bd.r);
        c = mix(c, P(15).rgb * ratio, bd.g);
        float patchy = 0.6 + 0.6 * grime;
        float4 lr = P(28), lm = P(29);
        float cv;
        cv = COVER(lay.r, amt.x); c = mix(c, P(23).rgb * patchy * ratioAll, cv);
        rough = mix(rough, saturate(rough * lr.x), cv); metalness = mix(metalness, saturate(metalness + lm.x), cv);
        cv = COVER(lay.g, amt.y); c = mix(c, P(24).rgb * patchy * ratioAll, cv);
        rough = mix(rough, saturate(rough * lr.y), cv); metalness = mix(metalness, saturate(metalness + lm.y), cv);
        cv = COVER(lay.b, amt.z); c = mix(c, P(25).rgb * patchy * ratioAll, cv);
        rough = mix(rough, saturate(rough * lr.z), cv); metalness = mix(metalness, saturate(metalness + lm.z), cv);
        float c1 = COVER(aux.a, amt2.x), c2 = COVER(aux.a, amt2.x * 0.45);
        c = mix(c, P(27).rgb * ratioAll, c1);
        c = mix(c, P(27).rgb * 0.45 * ratioAll, c2);
        float charred = saturate((lay.a - (1.0 - min(1.0, amt.w * 2.5))) / amt2.y);
        float ember = saturate((lay.a - (1.0 - amt.w * 0.3)) / 0.04);
        c *= 1.0 - 0.88 * charred;
        float ml = saturate(dot(lineTex.sample(s, uv), P(16)) + aux.r * finish.z);
        float4 lineColor = P(17);
        c = c * (1.0 - ml * misc.x) + ml * misc.x * 0.6 * lineColor.rgb;
        float3 e = _surface.emission.rgb;
        float le = dot(e, float3(0.2126, 0.7152, 0.0722));
        _surface.emission.rgb = (mix(e, P(3).rgb * (le / refA.w) * 1.6, mk.a)
                                 + lineColor.rgb * (ml * lineColor.w * 1.6) + P(26).rgb * (ember * 2.4)) * misc.y;
        """
        if CommandLine.arguments.contains("--debug-no-shader") { return m }
        // All look values live in a small float texture (see writeParams): SceneKit silently draws a shader modifier
        // magenta beyond about 23 custom arguments, and 1.11 alone adds a dozen values.
        m.shaderModifiers = [.surface: """
        #pragma arguments
        texture2d<float> maskTexture;
        texture2d<float> msTexture;
        texture2d<float> badgeTex;
        texture2d<float> lineTex;
        texture2d<float> auxTexture;
        texture2d<float> layerTex;
        texture2d<float> params;
        #pragma body
        #define P(i) params.read(uint2(i, 0))
        #define COVER(f, a) saturate(((f) - (1.0 - (a))) / amt2.y)
        constexpr sampler s(filter::linear, mip_filter::linear, address::repeat);
        constexpr sampler sn(filter::nearest, address::repeat);
        float2 uv = _surface.diffuseTexcoord;
        float4 mk = maskTexture.sample(s, uv);
        float4 aux = auxTexture.sample(s, uv);    // material pattern and camo field filtered
        aux.g = auxTexture.sample(sn, uv).g;      // the zone number must not be interpolated
        float4 ms = msTexture.sample(s, uv);
        float4 refA = P(7), refB = P(8);
        float3 c = _surface.diffuse.rgb;
        float lum = max(dot(c, float3(0.2126, 0.7152, 0.0722)), 1e-5);
        float rough = 1.0 - ms.a;
        float metalness = ms.r;
        \(body)
        _surface.diffuse.rgb = c;
        _surface.roughness = rough;
        _surface.metalness = metalness;
        """]
        for (key, tex) in sharedTextures() { m.setValue(SCNMaterialProperty(contents: tex), forKey: key) }
        m.setValue(SCNMaterialProperty(contents: paramTexture as Any), forKey: "params")
        return m
    }

    /// Mask textures shared by every scene of the same ViewerData (A/B, exports): packed once.
    private func sharedTextures() -> [(String, Any)] {
        let cfg = data.config
        func chan(_ list: [MaskChannel]?, _ i: Int) -> (String, Int)? {
            guard let l = list, i < l.count else { return nil }
            return (l[i].texture, l[i].channel)
        }
        let key = data.root.path
        if let cached = RobotScene.packCache[key] as? [(String, Any)] { return cached }
        let black = NSImage(size: NSSize(width: 4, height: 4), flipped: false) { r in
            NSColor.black.setFill(); r.fill(); return true
        }
        let lines = cfg.linePatterns ?? []
        let layers = (cfg.layers ?? []).map { MaskChannel(id: $0.id, texture: $0.texture, channel: $0.channel) }
        let idx = { (id: String) in layers.firstIndex { $0.id == id } ?? 99 }
        let props: [(String, Any?)] = [
            ("maskTexture", packed([("T_Robot_ColorMask.png", 0), ("T_Robot_ColorMask.png", 1),
                                    ("T_Robot_ColorMask.png", 2), ("T_Robot_LightMask.png", 0)])),
            // metal/smoothness, plus the wear fields in the free channels
            ("msTexture", packed([("T_Robot_MetallicSmoothness.png", 0), chan(layers, idx("Wear")),
                                  chan(layers, idx("Grime")), ("T_Robot_MetallicSmoothness.png", 3)])),
            ("lineTex", packed((0..<4).map { chan(lines, $0) })),
            ("layerTex", packed(["Rust", "Moss", "Frost", "Embers"].map { chan(layers, idx($0)) })),
        ]
        let result = props.map { ($0.0, $0.1 ?? black as Any) }
        RobotScene.packCache[key] = result
        return result
    }

    /// aux: R = fifth glow pattern (Joints), G = paint zone number, B = pattern of the chosen material, A = camo field.
    /// The R/G/A bytes are packed once per ViewerData; a material change only fills channel B (one texture alive).
    private func auxTexture(material: String) -> Any {
        let cfg = data.config
        let key = data.root.path + "#auxBase"
        if RobotScene.packCache[key] == nil {
            let lines = cfg.linePatterns ?? []
            let camo = (cfg.layers ?? []).first { $0.id == "Camo" }
            RobotScene.packCache[key] = packBytes([lines.count > 4 ? (lines[4].texture, lines[4].channel) : nil, nil, nil,
                                                   camo.map { ($0.texture, $0.channel) }],
                                                  zoneIDs: (cfg.zones ?? []).map { "\($0.texture)#\($0.channel)" },
                                                  zoneChannel: 1) as Any
        }
        guard let (w, h, base) = RobotScene.packCache[key] as? (Int, Int, [UInt8]) else { return NSNull() }
        var bytes = base
        if let mat = (cfg.materials ?? []).first(where: { $0.id == material }),
           let (mw, mh, mp) = pixels(mat.texture), mw == w, mh == h {
            for i in 0..<(w * h) { bytes[i * 4 + 2] = mp[i * 4 + mat.channel] }
        }
        return RobotScene.texture(w, h, bytes) as Any
    }

    /// The look's two badges as one texture (R, G): any number of badge masks, one sampler. Packed on a badge change.
    private func badgeTexture(_ ids: [String]) -> Any {
        let chans = (data.config.badges ?? []).reduce(into: [String: (String, Int)]()) { $0[$1.id] = ($1.texture, $1.channel) }
        let channels: [(String, Int)?] = [0, 1].map { $0 < ids.count ? chans[ids[$0]] : nil } + [nil, nil]
        let none = RobotScene.texture(4, 4, [UInt8](repeating: 0, count: 64)) as Any
        if channels.allSatisfy({ $0 == nil }) { return none }
        return packed(channels, keepPixels: true) ?? none       // a missing mask file: no badge, not an unbound sampler
    }

    /// The look as 32 float4 texels (layout: the P(i) indices in the shader).
    private var params = [SIMD4<Float>](repeating: .zero, count: 32)
    private var shownMaterial = "?"
    private var shownBadges: [String]? = nil
    private var emitterNodes: [(object: String, node: SCNNode)] = []

    /// Soft grey puffs that rise, grow and fade (chimney smoke).
    private static func smoke(direction: SIMD3<Float>) -> SCNParticleSystem {
        let ps = SCNParticleSystem()
        let size = 64
        var px = [UInt8](repeating: 0, count: size * size * 4)
        for y in 0..<size { for x in 0..<size {
            let dx = (Float(x) + 0.5) / Float(size) * 2 - 1, dy = (Float(y) + 0.5) / Float(size) * 2 - 1
            let a = max(0, 1 - (dx * dx + dy * dy))
            let i = (y * size + x) * 4
            let v = UInt8(a * a * 255)          // premultiplied white
            px[i] = v; px[i + 1] = v; px[i + 2] = v; px[i + 3] = v
        } }
        let ctx = CGContext(data: &px, width: size, height: size, bitsPerComponent: 8, bytesPerRow: size * 4,
                            space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        ps.particleImage = ctx?.makeImage()
        ps.birthRate = 9
        ps.warmupDuration = 2       // snapshots and the first frame already show a plume
        ps.particleLifeSpan = 1.8
        ps.particleLifeSpanVariation = 0.4
        ps.particleSize = 0.035
        ps.particleColor = NSColor(white: 0.78, alpha: 0.55)
        ps.particleVelocity = 0.22
        ps.particleVelocityVariation = 0.06
        ps.spreadingAngle = 12
        ps.emittingDirection = SCNVector3(direction.x, direction.y, direction.z)
        ps.acceleration = SCNVector3(0, 0.05, 0)
        ps.blendMode = .alpha
        ps.isLightingEnabled = false
        let grow = CAKeyframeAnimation()
        grow.values = [1.0, 2.6, 4.0]
        let fade = CAKeyframeAnimation()
        fade.values = [0.0, 0.7, 0.0]
        fade.keyTimes = [0, 0.15, 1]
        ps.propertyControllers = [.size: SCNParticlePropertyController(animation: grow),
                                  .opacity: SCNParticlePropertyController(animation: fade)]
        return ps
    }

    /// Emitters follow the visible accessories.
    private func updateEmitters(_ look: Look) {
        for (object, node) in emitterNodes {
            let parts = object.split(separator: "_", maxSplits: 1).map(String.init)
            node.isHidden = parts.count != 2 || look.variants[parts[0]] != parts[1]
        }
    }
    private lazy var paramTexture: MTLTexture? = {
        let desc = MTLTextureDescriptor.texture2DDescriptor(pixelFormat: .rgba32Float, width: 32, height: 1,
                                                            mipmapped: false)
        desc.usage = .shaderRead
        return RobotScene.device?.makeTexture(descriptor: desc)
    }()

    private func writeParams() {
        params.withUnsafeBytes { buf in
            paramTexture?.replace(region: MTLRegionMake2D(0, 0, 32, 1), mipmapLevel: 0, withBytes: buf.baseAddress!,
                                  bytesPerRow: 32 * 16)
        }
    }

    private static func f4(_ c: RGB, _ w: Double = 0) -> SIMD4<Float> {
        SIMD4(Float(c.r), Float(c.g), Float(c.b), Float(w))
    }

    private static func f4(_ v: [Double]) -> SIMD4<Float> {
        SIMD4(Float(v[0]), Float(v[1]), Float(v[2]), Float(v.count > 3 ? v[3] : 0))
    }

    /// One-hot channel weights per mask texture (line patterns: 2 textures x RGBA).
    private func maskSelector(_ id: String?, in channels: [MaskChannel]) -> [[Double]] {
        var sel = [[Double]](repeating: [0, 0, 0, 0], count: 2)
        guard let id = id, let ch = channels.first(where: { $0.id == id }) else { return sel }
        let textures = Array(NSOrderedSet(array: channels.map(\.texture))) as? [String] ?? []
        let t = textures.firstIndex(of: ch.texture) ?? 0
        if t < 2 && ch.channel < 4 { sel[t][ch.channel] = 1 }
        return sel
    }

    private func vec4(_ v: [Double]) -> NSValue {
        NSValue(scnVector4: SCNVector4(CGFloat(v[0]), CGFloat(v[1]), CGFloat(v[2]), CGFloat(v[3])))
    }

    private func vec(_ c: RGB, w: Double? = nil) -> NSValue {
        NSValue(scnVector4: SCNVector4(CGFloat(c.r), CGFloat(c.g), CGFloat(c.b), CGFloat(w ?? c.luminance)))
    }

    private func buildRig() {
        let doc = data.doc
        for b in doc.bones {
            let n = SCNNode()
            n.name = b.name
            n.simdTransform = b.restMatrix
            boneNodes[b.name] = n
            boneOrder.append(n)
        }
        for b in doc.bones {
            let n = boneNodes[b.name]!
            if let p = b.parent, let pn = boneNodes[p] { pn.addChildNode(n) } else { robotRoot.addChildNode(n) }
        }
        scene.rootNode.addChildNode(robotRoot)
        robotRoot.addChildNode(propRoot)
        for e in data.config.emitters ?? [] {      // the rig is still in its rest pose here
            guard let bone = boneNodes[e.bone] else { continue }
            let node = SCNNode()
            node.simdPosition = bone.simdConvertPosition(EmitterInfo.scn(e.position), from: robotRoot)
            node.addParticleSystem(RobotScene.smoke(direction: bone.simdConvertVector(EmitterInfo.scn(e.direction),
                                                                                     from: robotRoot)))
            node.isHidden = true
            bone.addChildNode(node)
            emitterNodes.append(("\(e.slot)_\(e.variant)", node))
        }

        let stride = doc.vertexStride * 4
        for part in doc.parts {
            let vdata = data.vertices.subdata(in: part.vertexOffset * stride ..< (part.vertexOffset + part.vertexCount) * stride)
            let idata = data.indices.subdata(in: part.indexOffset * 4 ..< (part.indexOffset + part.indexCount) * 4)
            let pos = SCNGeometrySource(data: vdata, semantic: .vertex, vectorCount: part.vertexCount,
                                        usesFloatComponents: true, componentsPerVector: 3, bytesPerComponent: 4,
                                        dataOffset: 0, dataStride: stride)
            let nrm = SCNGeometrySource(data: vdata, semantic: .normal, vectorCount: part.vertexCount,
                                        usesFloatComponents: true, componentsPerVector: 3, bytesPerComponent: 4,
                                        dataOffset: 12, dataStride: stride)
            let uv = SCNGeometrySource(data: vdata, semantic: .texcoord, vectorCount: part.vertexCount,
                                       usesFloatComponents: true, componentsPerVector: 2, bytesPerComponent: 4,
                                       dataOffset: 24, dataStride: stride)
            let el = SCNGeometryElement(data: idata, primitiveType: .triangles, primitiveCount: part.indexCount / 3,
                                        bytesPerIndex: 4)
            let geo = SCNGeometry(sources: [pos, nrm, uv], elements: [el])
            switch part.material {
            case "fabric": geo.materials = [fabricMat]
            case "eyes": geo.materials = [eyeMat]
            case "display": geo.materials = [displayMat]
            default: geo.materials = [bodyMat]
            }
            var node = SCNNode(geometry: geo)
            if part.material == "eyes" { node.renderingOrder = 10 }
            if part.spin, let pv = part.pivot {
                let pivot = SCNNode()
                pivot.simdPosition = SIMD3(pv[0], pv[1], pv[2])
                node.simdPosition = -SIMD3(pv[0], pv[1], pv[2])
                pivot.addChildNode(node)
                spinNodes.append(pivot)
                node = pivot
            }
            boneNodes[part.bone]?.addChildNode(node)
            objectNodes[part.object, default: []].append(node)
        }
    }

    private func buildEnvironment() {
        scene.background.contents = NSColor(calibratedRed: 0.035, green: 0.05, blue: 0.07, alpha: 1)
        scene.lightingEnvironment.contents = RobotScene.environmentImage()
        scene.lightingEnvironment.intensity = 1.2

        func light(_ type: SCNLight.LightType, _ intensity: CGFloat, _ color: NSColor, _ pos: SIMD3<Float>,
                   shadow: Bool = false) {
            let l = SCNLight()
            l.type = type
            l.intensity = intensity
            l.color = color
            l.castsShadow = shadow && !CommandLine.arguments.contains("--debug-no-shadow")
            if shadow {
                l.orthographicScale = 2.2
                l.zNear = 0.5
                l.zFar = 12
                l.shadowBias = 1.5
                l.shadowMode = .deferred
                l.shadowSampleCount = 8
                l.shadowRadius = 4
                l.shadowColor = NSColor(white: 0, alpha: 0.55)
            }
            let n = SCNNode()
            n.light = l
            n.simdPosition = pos
            n.simdLook(at: SIMD3(0, 0.6, 0), up: SIMD3(0, 1, 0), localFront: SCNNode.simdLocalFront)
            scene.rootNode.addChildNode(n)
        }
        light(.directional, 1600, NSColor(calibratedRed: 1, green: 0.95, blue: 0.88, alpha: 1), [-2.2, 3.0, 2.6], shadow: true)
        light(.directional, 500, NSColor(calibratedRed: 0.75, green: 0.88, blue: 1, alpha: 1), [2.6, 1.4, 1.8])
        light(.directional, 1100, NSColor(calibratedRed: 0.35, green: 0.85, blue: 1, alpha: 1), [1.2, 2.2, -2.8])
        let amb = SCNLight()
        amb.type = .ambient
        amb.intensity = 180
        amb.color = NSColor(calibratedRed: 0.6, green: 0.7, blue: 0.85, alpha: 1)
        let an = SCNNode()
        an.light = amb
        scene.rootNode.addChildNode(an)

        let f = SCNFloor()
        f.reflectivity = 0.12
        f.reflectionFalloffEnd = 1.5
        let fm = SCNMaterial()
        fm.lightingModel = .physicallyBased
        fm.diffuse.contents = RobotScene.gridImage()
        fm.diffuse.contentsTransform = SCNMatrix4MakeScale(8, 8, 1)
        fm.diffuse.wrapS = .repeat
        fm.diffuse.wrapT = .repeat
        fm.roughness.contents = 0.55
        fm.metalness.contents = 0.3
        f.materials = [fm]
        floor = SCNNode(geometry: f)
        scene.rootNode.addChildNode(floor)

        let cam = SCNCamera()
        cam.fieldOfView = 32
        cam.zNear = 0.02
        cam.zFar = 60
        cam.wantsHDR = true
        cam.bloomIntensity = 0.9
        cam.bloomThreshold = 0.75
        cam.bloomBlurRadius = 8
        cam.wantsExposureAdaptation = false
        cam.exposureOffset = -0.2
        cameraNode.camera = cam
        scene.rootNode.addChildNode(cameraNode)
        setView("Iso")
    }

    static func gridImage() -> NSImage {
        let size = 256
        let img = NSImage(size: NSSize(width: size, height: size))
        img.lockFocus()
        NSColor(calibratedRed: 0.05, green: 0.06, blue: 0.075, alpha: 1).setFill()
        NSRect(x: 0, y: 0, width: size, height: size).fill()
        NSColor(calibratedRed: 0.13, green: 0.17, blue: 0.2, alpha: 1).setFill()
        NSRect(x: 0, y: 0, width: size, height: 3).fill()
        NSRect(x: 0, y: 0, width: 3, height: size).fill()
        img.unlockFocus()
        return img
    }

    static func environmentImage() -> NSImage {
        let w = 512, h = 256
        let img = NSImage(size: NSSize(width: w, height: h))
        img.lockFocus()
        let g = NSGradient(colors: [NSColor(calibratedRed: 0.02, green: 0.03, blue: 0.04, alpha: 1),
                                    NSColor(calibratedRed: 0.18, green: 0.25, blue: 0.32, alpha: 1),
                                    NSColor(calibratedRed: 0.55, green: 0.65, blue: 0.75, alpha: 1)])
        g?.draw(in: NSRect(x: 0, y: 0, width: w, height: h), angle: 90)
        NSColor(calibratedWhite: 1, alpha: 0.9).setFill()
        NSBezierPath(ovalIn: NSRect(x: 90, y: 170, width: 70, height: 40)).fill()
        NSColor(calibratedRed: 0.4, green: 0.9, blue: 1, alpha: 0.8).setFill()
        NSBezierPath(ovalIn: NSRect(x: 330, y: 150, width: 60, height: 30)).fill()
        img.unlockFocus()
        return img
    }

    // MARK: - camera

    static let views = ["Iso", "Front", "Side", "Back", "Face"]

    private(set) var currentView = "Iso"

    func setView(_ name: String) {
        currentView = name
        let target = SIMD3<Float>(0, 0.66, 0)
        var pos: SIMD3<Float>
        switch name {
        case "Front": pos = [0, 0.72, 4.3]
        case "Side": pos = [4.3, 0.72, 0]
        case "Back": pos = [0, 0.72, -4.3]
        case "Face": pos = [0.35, 1.2, 1.45]
        default: pos = [-3.0, 2.6, 3.0]
        }
        cameraNode.simdPosition = pos
        cameraNode.simdLook(at: name == "Face" ? SIMD3(0, 1.1, 0) : target, up: SIMD3(0, 1, 0),
                            localFront: SCNNode.simdLocalFront)
    }

    var showFloor: Bool {
        get { !floor.isHidden }
        set { floor.isHidden = !newValue }
    }

    // MARK: - look

    func apply(look: Look) {
        lock.lock()
        self.look = look
        lock.unlock()
        // visibility per slot variant
        var visible = Set(["Robot_Body"])
        for (slot, variant) in look.variants where variant != "None" { visible.insert("\(slot)_\(variant)") }
        for slot in data.config.slots where look.variants[slot.id] == nil {
            if let first = slot.variants.first, first != "None" { visible.insert("\(slot.id)_\(first)") }
        }
        heldItemNodes = []
        for (obj, nodes) in objectNodes {
            for n in nodes { n.isHidden = !visible.contains(obj) }
            if obj.hasPrefix("AccHand_") && visible.contains(obj) { heldItemNodes += nodes }
        }
        heldItemVisible = true
        let cfg = data.config
        var (rf, mo) = look.finish.params(cfg)
        let material = (cfg.materials ?? []).first { $0.id == look.material.id }
        if let mat = material, look.finish == .standard {       // the material brings its own surface
            rf = mat.roughness ?? rf
            mo = mat.metalOffset ?? mo
        }
        let lum = { (c: [Double]) in RGB(c).luminance }
        var p = [SIMD4<Float>](repeating: .zero, count: 32)
        for (i, c) in [look.armor, look.mech, look.metal, look.lights, look.visor, look.fabric, look.armor2].enumerated() {
            p[i] = RobotScene.f4(c)
        }
        p[7] = RobotScene.f4([lum(classic.armor), lum(classic.mech), lum(classic.metal), lum(classic.accent)])
        p[8] = RobotScene.f4([lum(classic.visor), 0.71, 0, 0])
        let joints = maskSelector(look.lines.pattern, in: cfg.linePatterns ?? [])[1][0]
        var zoneBits = 0
        for (i, z) in (cfg.zones ?? []).enumerated() where look.zones2.contains(z.id) { zoneBits |= 1 << i }
        p[9] = RobotScene.f4([rf, mo, joints, Double(zoneBits)])
        for (i, badge) in [0, 1].map({ $0 < look.badges.count ? look.badges[$0] : nil }).enumerated() {
            p[14 + i] = RobotScene.f4(badge?.color ?? RGB(0, 0, 0))     // P(10..13) are free since 1.12
        }
        p[16] = RobotScene.f4(maskSelector(look.lines.pattern, in: cfg.linePatterns ?? [])[0])
        p[17] = RobotScene.f4(look.lines.color, look.lines.strength)
        p[18] = SIMD4(Float(cfg.linePaint ?? 0.85), 1, Float(look.wear), material != nil ? 1 : 0)
        p[19] = RobotScene.f4(look.material.color)
        let weathering = cfg.weathering
        func amount(_ id: String) -> Float { Float(look.layers.first { $0.id == id }?.amount ?? 0) }
        p[21] = SIMD4(amount("Rust"), amount("Moss"), amount("Frost"), amount("Embers"))
        p[22] = SIMD4(amount("Camo"), Float(weathering?.coverSoft ?? 0.1), Float(weathering?.wearDefault ?? 0.5), 0)
        let infos = cfg.layers ?? []
        for (i, id) in ["Rust", "Moss", "Frost", "Embers", "Camo"].enumerated() {
            let info = infos.first { $0.id == id }
            let col = look.layers.first { $0.id == id }?.color ?? RGB(info?.color ?? [0, 0, 0])
            p[23 + i] = RobotScene.f4(col)
            if i < 4 {
                p[28][i] = Float(info?.roughness ?? 1)
                p[29][i] = Float(info?.metalOffset ?? 0)
            }
        }
        p[31] = RobotScene.f4(weathering?.bareMetal ?? [0.62, 0.63, 0.65])
        params = p
        writeParams()
        let matID = material?.id ?? "None"
        if matID != shownMaterial {
            shownMaterial = matID
            let aux = auxTexture(material: matID)
            for m in [bodyMat, fabricMat] { m.setValue(SCNMaterialProperty(contents: aux), forKey: "auxTexture") }
        }
        let badgeIDs = look.badges.prefix(2).map(\.id)
        if badgeIDs != shownBadges {
            shownBadges = badgeIDs
            let tex = badgeTexture(badgeIDs)
            for m in [bodyMat, fabricMat] { m.setValue(SCNMaterialProperty(contents: tex), forKey: "badgeTex") }
        }
        updateEmitters(look)
        shownExpression = ""            // eye colour or style may have changed
        displayMat.setValue(vec(look.screen), forKey: "screenColor")
        shownPower = -1
        setPower(1.0, glow: look.glow)
        setDisplay(look.display)
        setExpression(look.expression)
    }

    private func cell(index: Int, grid: [Int], cellHeight: Double? = nil) -> NSValue {
        let cols = grid[0], rows = grid[1]
        let cw = 1.0 / Double(cols)
        let ch = cellHeight ?? 1.0 / Double(rows)
        let col = index % cols, row = index / cols
        return NSValue(scnVector4: SCNVector4(CGFloat(cw), CGFloat(ch), CGFloat(Double(col) * cw), CGFloat(Double(row) * ch)))
    }

    func setExpression(_ name: String) {
        wantedExpression = name
        refreshEyes()
    }

    /// Shows the wanted expression - or the blink cell while blinking (only over expressions the blink rule names).
    private func refreshEyes() {
        var name = wantedExpression
        if let b = data.config.blink, blinkClock < blinkUntil, b.during.contains(name) { name = b.expression }
        guard name != shownExpression, let i = data.config.expressions.firstIndex(of: name) else { return }
        shownExpression = name
        eyeMat.setValue(cell(index: i, grid: data.config.eyeGrid), forKey: "cell")
        let style = data.config.expressionStyles?[name]
        lock.lock()
        let eyes = look.eyes
        lock.unlock()
        eyeMat.setValue(vec(style?.tint.map { RGB($0) } ?? eyes), forKey: "eyeColor")
        eyeGlowStyle = style?.glow ?? 1.0
        shownPower = -1             // re-applies the glow with the style factor (also without a playing clip)
        setPower(lastPower, glow: lastGlow)
    }

    /// Idle blinking (robot_config.json > blink): random interval, short duration; GUI only (snapshots never blink).
    private func updateBlink(_ time: TimeInterval) {
        guard let b = data.config.blink, b.interval.count == 2 else { return }
        blinkClock = time
        if nextBlink == 0 { nextBlink = time + Double.random(in: b.interval[0]...b.interval[1]) }
        if time >= nextBlink {
            blinkUntil = time + b.duration
            nextBlink = time + Double.random(in: b.interval[0]...b.interval[1])
        }
        refreshEyes()
    }

    func setDisplay(_ name: String) {
        guard name != shownDisplay else { return }
        if let anim = data.config.displayAnimations?.first(where: { $0.name == name }), let fb = data.config.flipbook,
           let atlas = flipbookAtlas {
            shownDisplay = name
            displayAnim = anim
            displayFrame = -1
            displayMat.setValue(SCNMaterialProperty(contents: atlas), forKey: "atlas")
            setDisplayFrame(0, fb)
            return
        }
        guard let i = data.config.displays.firstIndex(of: name) else { return }
        if displayAnim != nil {
            displayAnim = nil
            displayMat.setValue(SCNMaterialProperty(contents: staticDisplayAtlas as Any), forKey: "atlas")
        }
        shownDisplay = name
        displayMat.setValue(cell(index: i, grid: data.config.displayGrid, cellHeight: data.config.displayCellHeight),
                            forKey: "cell")
    }

    private func setDisplayFrame(_ frame: Int, _ fb: FlipbookInfo) {
        guard let anim = displayAnim, frame != displayFrame else { return }
        displayFrame = frame
        displayMat.setValue(NSValue(scnVector4: SCNVector4(CGFloat(fb.cell[0]), CGFloat(fb.cell[1]),
                                                           CGFloat(Double(frame) * fb.cell[0]),
                                                           CGFloat(Double(anim.row) * fb.cell[1]))), forKey: "cell")
    }

    /// Advances an animated display motif (flipbook) with the wall clock.
    private func updateDisplayAnimation(_ time: TimeInterval) {
        guard let anim = displayAnim, let fb = data.config.flipbook else { return }
        setDisplayFrame(Int(time * anim.fps) % max(anim.frames, 1), fb)
    }

    /// "Power" of all emissive parts: eyes, chest display and lights (Death switches off, Respawn on).
    func setPower(_ p: Double, glow: Double) {
        lastPower = p
        lastGlow = glow
        let key = p * 1000 + glow
        guard abs(key - shownPower) > 1e-4 else { return }
        shownPower = key
        params[18][1] = Float(min(glow, 1.6) * p)
        writeParams()
        eyeMat.setValue(NSNumber(value: glow * p * eyeGlowStyle), forKey: "glow")
        displayMat.setValue(NSNumber(value: min(p, 1.5)), forKey: "power")
    }

    // MARK: - helper geometry (ledge, bar, ladder, box) to check contacts

    var showProps: Bool {
        get { propsWanted }
        set { propsWanted = newValue; propRoot.isHidden = !newValue }
    }

    private func updateProps(for anim: AnimInfo?) {
        let name = anim?.name ?? ""
        guard name != propClip else { return }
        propClip = name
        propRoot.childNodes.forEach { $0.removeFromParentNode() }
        guard let p = anim.flatMap({ data.config.clips?[$0.name]?.prop }) else { return }
        let mat = SCNMaterial()
        mat.lightingModel = .physicallyBased
        mat.diffuse.contents = NSColor(calibratedRed: 0.42, green: 0.47, blue: 0.52, alpha: 1)
        mat.roughness.contents = 0.7
        mat.metalness.contents = 0.1
        let edgeMat = SCNMaterial()
        edgeMat.lightingModel = .constant
        edgeMat.diffuse.contents = NSColor(calibratedRed: 1.0, green: 0.75, blue: 0.2, alpha: 1)

        func add(_ g: SCNGeometry, _ pos: SIMD3<Float>, _ m: SCNMaterial = mat, euler: SIMD3<Float> = .zero) {
            g.materials = [m]
            let n = SCNNode(geometry: g)
            n.simdPosition = pos
            n.simdEulerAngles = euler
            propRoot.addChildNode(n)
        }
        let f = { (v: Double?) in Float(v ?? 0) }
        switch p.type {
        case "ledge":
            let top = f(p.top), edge = f(p.edge), depth: Float = 0.7
            add(SCNBox(width: 1.8, height: CGFloat(top), length: CGFloat(depth), chamferRadius: 0.004),
                [0, top / 2, edge + depth / 2])
            add(SCNBox(width: 1.8, height: 0.006, length: 0.006, chamferRadius: 0), [0, top + 0.003, edge], edgeMat)
        case "bar":
            add(SCNCylinder(radius: 0.022, height: 1.4), [0, f(p.up), f(p.forward)], euler: [0, 0, .pi / 2])
            for x: Float in [-0.7, 0.7] {
                add(SCNCylinder(radius: 0.03, height: CGFloat(f(p.up))), [x, f(p.up) / 2, f(p.forward)])
            }
        case "ladder":
            let plane = f(p.forward), rail = f(p.rail)
            for x in [-rail, rail] { add(SCNCylinder(radius: 0.018, height: 2.2), [x, 1.1, plane + 0.03]) }
            var y: Float = 0.25
            while y < 2.1 {
                add(SCNCylinder(radius: 0.014, height: CGFloat(2 * rail)), [0, y, plane + 0.03], euler: [0, 0, .pi / 2])
                y += f(p.spacing)
            }
        case "box":
            let face = f(p.forward), h = f(p.top)
            add(SCNBox(width: 0.9, height: CGFloat(h), length: 0.8, chamferRadius: 0.02), [0, h / 2, face + 0.4])
        default:
            break
        }
        propRoot.isHidden = !propsWanted
    }

    // MARK: - animation

    func play(_ anim: AnimInfo?, restart: Bool = true) {
        lock.lock()
        clip = anim
        if restart { clipTime = 0 }
        lock.unlock()
        updateProps(for: anim)
        if anim == nil {
            setPower(1.0, glow: look.glow)
            resetPose()
            for n in heldItemNodes { n.isHidden = false }
            heldItemVisible = true
            lock.lock()
            let lk = look
            lock.unlock()
            setExpression(lk.expression)
            setDisplay(lk.display)
        }
    }

    func setPlayback(playing: Bool, speed: Double, loop: Bool, turntable: Bool) {
        lock.lock()
        self.playing = playing
        self.speed = speed
        self.loopClip = loop
        self.turntable = turntable
        lock.unlock()
    }

    func seek(fraction: Double) {
        lock.lock()
        if let c = clip { clipTime = fraction * Double(c.frames) / data.doc.fps }
        lock.unlock()
        evaluate()
    }

    func resetPose() {
        for (i, b) in data.doc.bones.enumerated() { boneOrder[i].simdTransform = b.restMatrix }
    }

    func renderer(_ renderer: SCNSceneRenderer, updateAtTime time: TimeInterval) {
        let dt = lastTime.map { min(0.1, time - $0) } ?? 0
        lastTime = time
        lock.lock()
        if playing { clipTime += dt * speed }
        let spin = turntable
        lock.unlock()
        if spin { robotRoot.simdEulerAngles.y += Float(dt * 0.6) }
        for n in spinNodes { n.simdLocalRotate(by: simd_quatf(angle: Float(dt * 14), axis: [0, 0, 1])) }
        evaluate()
        updateDisplayAnimation(time)
        lock.lock()
        let auto = look.autoExpression
        lock.unlock()
        if auto { updateBlink(time) }
    }

    /// Sets bone transforms for the current clip time (also used for offscreen snapshots).
    func evaluate() {
        lock.lock()
        guard let c = clip else { lock.unlock(); return }
        let fps = data.doc.fps
        let frames = Double(c.frames)
        var f = clipTime * fps
        if loopClip || c.loop {
            f = f.truncatingRemainder(dividingBy: max(frames, 1))
            if f < 0 { f += frames }
        } else {
            f = min(f, frames)
        }
        let lk = look
        lock.unlock()
        let f0 = Int(f.rounded(.down)), f1 = min(f0 + 1, c.frames)
        let t = Float(f - Double(f0))
        let nb = data.doc.bones.count
        let k = data.doc.floatsPerBoneFrame
        let a = data.anim
        for i in 0..<nb {
            let o0 = c.offset + (f0 * nb + i) * k
            let o1 = c.offset + (f1 * nb + i) * k
            let p = simd_mix(SIMD3(a[o0], a[o0 + 1], a[o0 + 2]), SIMD3(a[o1], a[o1 + 1], a[o1 + 2]), SIMD3(repeating: t))
            let q0 = simd_quatf(ix: a[o0 + 3], iy: a[o0 + 4], iz: a[o0 + 5], r: a[o0 + 6])
            let q1 = simd_quatf(ix: a[o1 + 3], iy: a[o1 + 4], iz: a[o1 + 5], r: a[o1 + 6])
            let s = simd_mix(SIMD3(a[o0 + 7], a[o0 + 8], a[o0 + 9]), SIMD3(a[o1 + 7], a[o1 + 8], a[o1 + 9]),
                             SIMD3(repeating: t))
            let n = boneOrder[i]
            n.simdPosition = p
            n.simdOrientation = simd_slerp(q0, q1, t)
            n.simdScale = s
        }
        let meta = data.config.clips?[c.name]
        if lk.autoExpression, let m = meta {
            setExpression(ClipMeta.step(m.expression, f, base: lk.expression))
        } else {
            setExpression(lk.expression)
        }
        if lk.autoDisplay ?? true, let m = meta {
            setDisplay(ClipMeta.step(m.display, f, base: lk.display))
        } else {
            setDisplay(lk.display)
        }
        setPower(meta?.power(at: f) ?? 1.0, glow: lk.glow)
        let showItem = !(meta?.hideHeldItem ?? false)
        if showItem != heldItemVisible {
            heldItemVisible = showItem
            for n in heldItemNodes { n.isHidden = !showItem }
        }
        onFrame?(f / max(frames, 1))
    }

    // MARK: - snapshot

    func snapshot(size: CGSize) -> NSImage {
        let r = SCNRenderer(device: MTLCreateSystemDefaultDevice(), options: nil)
        r.scene = scene
        r.pointOfView = cameraNode
        return r.snapshot(atTime: 0, with: size, antialiasingMode: .multisampling4X)
    }
}

extension NSImage {
    func pngData() -> Data? {
        guard let tiff = tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return nil }
        return rep.representation(using: .png, properties: [:])
    }
}
