import AppKit
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
    private var shownDisplay = ""
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
        displayMat.setValue(SCNMaterialProperty(contents: image("T_Robot_DisplayAtlas.png") as Any), forKey: "atlas")
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
        c = fabricColor.rgb * (lum / max(fabricRef.w, 1e-4));
        rough = 0.75; metalness = 0.0;
        """ : """
        float vis = saturate(1.0 - (mk.r + mk.g + mk.b + mk.a));
        c = mix(c, armorColor.rgb * (lum / armorRef.w), mk.r);
        c = mix(c, mechColor.rgb * (lum / mechRef.w), mk.g);
        c = mix(c, metalColor.rgb * (lum / metalRef.w), mk.b);
        c = mix(c, lightsColor.rgb * (lum / lightsRef.w), mk.a);
        c = mix(c, visorColor.rgb * (lum / visorRef.w), vis);
        rough = mix(rough, saturate(rough * finish.x), mk.r);
        metalness = mix(metalness, saturate(metalness + finish.y), mk.r);
        rough = mix(rough, mix(rough, 0.04, 0.8), vis);
        float3 e = _surface.emission.rgb;
        float le = dot(e, float3(0.2126, 0.7152, 0.0722));
        _surface.emission.rgb = mix(e, lightsColor.rgb * (le / lightsRef.w) * 1.6, mk.a) * glowAll;
        """
        if CommandLine.arguments.contains("--debug-no-shader") { return m }
        m.shaderModifiers = [.surface: """
        #pragma arguments
        texture2d<float> maskTexture;
        texture2d<float> lightTexture;
        texture2d<float> msTexture;
        float4 armorColor;
        float4 mechColor;
        float4 metalColor;
        float4 lightsColor;
        float4 visorColor;
        float4 fabricColor;
        float4 armorRef;
        float4 mechRef;
        float4 metalRef;
        float4 lightsRef;
        float4 visorRef;
        float4 fabricRef;
        float4 finish;
        float glowAll;
        #pragma body
        constexpr sampler s(filter::linear, mip_filter::linear, address::repeat);
        float2 uv = _surface.diffuseTexcoord;
        float4 mk = float4(maskTexture.sample(s, uv).rgb, lightTexture.sample(s, uv).r);
        float4 ms = msTexture.sample(s, uv);
        float3 c = _surface.diffuse.rgb;
        float lum = max(dot(c, float3(0.2126, 0.7152, 0.0722)), 1e-5);
        float rough = 1.0 - ms.a;
        float metalness = ms.r;
        \(body)
        _surface.diffuse.rgb = c;
        _surface.roughness = rough;
        _surface.metalness = metalness;
        """]
        m.setValue(SCNMaterialProperty(contents: image("T_Robot_ColorMask.png") as Any), forKey: "maskTexture")
        m.setValue(SCNMaterialProperty(contents: image("T_Robot_LightMask.png") as Any), forKey: "lightTexture")
        m.setValue(SCNMaterialProperty(contents: image("T_Robot_MetallicSmoothness.png") as Any), forKey: "msTexture")
        let refs: [(String, [Double])] = [("armorRef", classic.armor), ("mechRef", classic.mech),
                                          ("metalRef", classic.metal), ("lightsRef", classic.accent),
                                          ("visorRef", classic.visor), ("fabricRef", [0.71, 0.71, 0.71])]
        for (k, v) in refs { m.setValue(vec(RGB(v)), forKey: k) }
        return m
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

    func setView(_ name: String) {
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
        let (rf, mo) = look.finish.params
        for m in [bodyMat, fabricMat] {
            m.setValue(vec(look.armor), forKey: "armorColor")
            m.setValue(vec(look.mech), forKey: "mechColor")
            m.setValue(vec(look.metal), forKey: "metalColor")
            m.setValue(vec(look.lights), forKey: "lightsColor")
            m.setValue(vec(look.visor), forKey: "visorColor")
            m.setValue(vec(look.fabric), forKey: "fabricColor")
            m.setValue(NSValue(scnVector4: SCNVector4(CGFloat(rf), CGFloat(mo), 0, 0)), forKey: "finish")
        }
        eyeMat.setValue(vec(look.eyes), forKey: "eyeColor")
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
        guard name != shownExpression, let i = data.config.expressions.firstIndex(of: name) else { return }
        shownExpression = name
        eyeMat.setValue(cell(index: i, grid: data.config.eyeGrid), forKey: "cell")
    }

    func setDisplay(_ name: String) {
        guard name != shownDisplay, let i = data.config.displays.firstIndex(of: name) else { return }
        shownDisplay = name
        displayMat.setValue(cell(index: i, grid: data.config.displayGrid, cellHeight: data.config.displayCellHeight),
                            forKey: "cell")
    }

    /// "Power" of all emissive parts: eyes, chest display and lights (Death switches off, Respawn on).
    func setPower(_ p: Double, glow: Double) {
        let key = p * 1000 + glow
        guard abs(key - shownPower) > 1e-4 else { return }
        shownPower = key
        for m in [bodyMat, fabricMat] { m.setValue(NSNumber(value: min(glow, 1.6) * p), forKey: "glowAll") }
        eyeMat.setValue(NSNumber(value: glow * p), forKey: "glow")
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
