import AppKit
import SceneKit
import SwiftUI
import UniformTypeIdentifiers

@MainActor
final class ViewerModel: ObservableObject {
    let data: RobotData
    let robot: RobotScene
    @Published var look = Look() { didSet { robot.apply(look: look) } }
    @Published var selectedSkin = "Classic"
    @Published var clip: AnimInfo? { didSet { robot.play(clip) } }
    @Published var playing = true { didSet { pushPlayback() } }
    @Published var speed = 1.0 { didSet { pushPlayback() } }
    @Published var loop = true { didSet { pushPlayback() } }
    @Published var turntable = false { didSet { pushPlayback() } }
    @Published var showFloor = true { didSet { robot.showFloor = showFloor } }
    @Published var showProps = true { didSet { robot.showProps = showProps } }
    @Published var progress = 0.0
    @Published var status = ""

    init(data: RobotData) {
        self.data = data
        robot = RobotScene(data: data)
        var l = Look()
        if let t = data.config.robotTypes.first { l.apply(type: t, config: data.config) }
        look = l
        robot.apply(look: l)
        clip = data.doc.animations.first { $0.name == "Idle" }
        robot.play(clip)
        robot.onFrame = { [weak self] p in
            DispatchQueue.main.async { self?.progress = p }
        }
    }

    var config: RobotConfig { data.config }

    func pushPlayback() { robot.setPlayback(playing: playing, speed: speed, loop: loop, turntable: turntable) }

    func applyType(_ t: RobotType) {
        var l = look
        l.apply(type: t, config: config)
        selectedSkin = t.skin
        look = l
    }

    func applySkin(_ name: String) {
        guard let s = config.skins.first(where: { $0.name == name }) else { return }
        var l = look
        l.apply(skin: s)
        look = l
        selectedSkin = name
    }

    func randomize() {
        var l = look
        for slot in config.slots {
            l.variants[slot.id] = slot.accessory && Bool.random() ? "None" : slot.variants.randomElement()
        }
        if let s = config.skins.randomElement() { l.apply(skin: s); selectedSkin = s.name }
        if Bool.random() {
            l.armor = RGB(.random(in: 0.02...0.95), .random(in: 0.02...0.95), .random(in: 0.02...0.95))
        }
        l.expression = config.expressions.filter { $0 != "Off" }.randomElement() ?? "Normal"
        l.display = config.displays.randomElement() ?? "Status"
        l.finish = Finish.allCases.randomElement() ?? .standard
        l.name = "Zufall"
        look = l
    }

    func saveLook() {
        let p = NSSavePanel()
        p.allowedContentTypes = [.json]
        p.nameFieldStringValue = "\(look.name).robotlook.json"
        guard p.runModal() == .OK, let url = p.url else { return }
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        do {
            try enc.encode(look).write(to: url)
            status = "Look gespeichert: \(url.lastPathComponent)"
        } catch { status = "Fehler beim Speichern: \(error.localizedDescription)" }
    }

    func loadLook() {
        let p = NSOpenPanel()
        p.allowedContentTypes = [.json]
        guard p.runModal() == .OK, let url = p.url else { return }
        do {
            let raw = try JSONDecoder().decode(Look.self, from: Data(contentsOf: url))
            let (clean, warnings) = raw.sanitized(config: config)
            look = clean
            status = "Look geladen: \(url.lastPathComponent)"
                + (warnings.isEmpty ? "" : " – ersetzt: " + warnings.joined(separator: ", "))
        } catch { status = "Datei ist kein gültiger Look: \(error.localizedDescription)" }
    }

    var clipMeta: ClipMeta? { clip.flatMap { config.clips?[$0.name] } }

    /// One line of game data for the playing clip (tempo, height, root motion, steps, current event).
    var clipInfo: String {
        guard let c = clip, let m = clipMeta else { return "" }
        var parts: [String] = []
        if m.naturalSpeed > 0 { parts.append(String(format: "Tempo %.2f m/s", m.naturalSpeed)) }
        parts.append(String(format: "Höhe %.2f–%.2f m", m.height.min, m.height.max))
        let mv = m.rootMotion.move
        if mv.contains(where: { abs($0) > 0.005 }) || abs(m.rootMotion.turnRight) > 0.5 {
            var r = "Root Motion"
            if abs(mv[2]) > 0.005 { r += String(format: " vor %.2f", mv[2]) }
            if abs(mv[1]) > 0.005 { r += String(format: " hoch %.2f", mv[1]) }
            if abs(mv[0]) > 0.005 { r += String(format: " rechts %.2f", mv[0]) }
            if abs(m.rootMotion.turnRight) > 0.5 { r += String(format: " Drehung %.0f°", m.rootMotion.turnRight) }
            parts.append(r)
        }
        if !m.footsteps.isEmpty { parts.append("Schritte " + m.footsteps.map { String(Int($0.frame)) }.joined(separator: ",")) }
        if m.hideHeldItem { parts.append("Gegenstand aus") }
        let f = progress * Double(c.frames)
        let recent = (m.events + m.footsteps).filter { $0.frame <= f && f - $0.frame < 5 }.last
        if let e = recent { parts.append("▶ " + e.value) }
        return parts.joined(separator: " · ")
    }

    func screenshot() {
        let p = NSSavePanel()
        p.allowedContentTypes = [.png]
        p.nameFieldStringValue = "Roboter_\(look.name).png"
        guard p.runModal() == .OK, let url = p.url else { return }
        let img = robot.snapshot(size: CGSize(width: 1600, height: 1600))
        do {
            try img.pngData()?.write(to: url)
            status = "Screenshot gespeichert: \(url.lastPathComponent)"
        } catch { status = "Fehler: \(error.localizedDescription)" }
    }
}

struct SceneContainer: NSViewRepresentable {
    let robot: RobotScene

    func makeNSView(context: Context) -> SCNView {
        let v = SCNView()
        v.scene = robot.scene
        v.pointOfView = robot.cameraNode
        v.delegate = robot
        v.allowsCameraControl = true
        v.defaultCameraController.interactionMode = .orbitTurntable
        v.defaultCameraController.target = SCNVector3(0, 0.66, 0)
        v.antialiasingMode = .multisampling4X
        v.rendersContinuously = true
        v.isPlaying = true
        v.preferredFramesPerSecond = 60
        return v
    }

    func updateNSView(_ nsView: SCNView, context: Context) {}
}

struct ContentView: View {
    @ObservedObject var model: ViewerModel

    var body: some View {
        HSplitView {
            ZStack(alignment: .bottomLeading) {
                SceneContainer(robot: model.robot)
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 6) {
                        ForEach(RobotScene.views, id: \.self) { v in
                            Button(v) { model.robot.setView(v) }
                        }
                        Toggle("Drehteller", isOn: $model.turntable).toggleStyle(.button)
                        Toggle("Boden", isOn: $model.showFloor).toggleStyle(.button)
                        Toggle("Hilfsobjekte", isOn: $model.showProps).toggleStyle(.button)
                            .help("Kante, Stange, Leiter oder Kiste aus den Clip-Daten einblenden")
                    }
                    if let c = model.clip {
                        HStack {
                            Button(model.playing ? "Pause" : "Play") { model.playing.toggle() }
                                .keyboardShortcut(.space, modifiers: [])
                            Text(c.name).font(.headline)
                            Slider(value: Binding(get: { model.progress }, set: { model.robot.seek(fraction: $0) }))
                                .frame(width: 220)
                            Text("Tempo")
                            Slider(value: $model.speed, in: 0.1...2.0).frame(width: 110)
                            Text(String(format: "%.1fx", model.speed)).monospacedDigit()
                        }
                    }
                    if !model.clipInfo.isEmpty { Text(model.clipInfo).font(.caption).monospacedDigit() }
                    if !model.status.isEmpty { Text(model.status).font(.caption).foregroundStyle(.secondary) }
                }
                .padding(10)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
                .padding(12)
            }
            .frame(minWidth: 560)

            Sidebar(model: model)
                .frame(minWidth: 330, idealWidth: 360, maxWidth: 440)
        }
    }
}

struct Sidebar: View {
    @ObservedObject var model: ViewerModel

    private func colorBinding(_ kp: WritableKeyPath<Look, RGB>) -> Binding<Color> {
        Binding(get: { model.look[keyPath: kp].color }, set: { model.look[keyPath: kp] = RGB(color: $0) })
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    Text("Cube Escape Roboter").font(.title2.bold())
                    Spacer()
                    Button("Zufall") { model.randomize() }
                }
                section("Robotertyp") {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 90))], spacing: 6) {
                        ForEach(model.config.robotTypes) { t in
                            Button(t.name) { model.applyType(t) }
                                .buttonStyle(.bordered)
                                .tint(model.look.name == t.name ? .accentColor : .gray)
                        }
                    }
                }
                section("Bauteile") {
                    ForEach(model.config.slots.filter { !$0.accessory }) { slot in variantPicker(slot) }
                }
                section("Accessoires") {
                    ForEach(model.config.slots.filter { $0.accessory }) { slot in variantPicker(slot) }
                }
                section("Farben") {
                    Picker("Skin", selection: Binding(get: { model.selectedSkin }, set: { model.applySkin($0) })) {
                        ForEach(model.config.skins) { Text($0.name).tag($0.name) }
                    }
                    LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible())], alignment: .leading) {
                        ColorPicker("Panzerung", selection: colorBinding(\.armor))
                        ColorPicker("Mechanik", selection: colorBinding(\.mech))
                        ColorPicker("Metall", selection: colorBinding(\.metal))
                        ColorPicker("Lichter", selection: colorBinding(\.lights))
                        ColorPicker("Augen", selection: colorBinding(\.eyes))
                        ColorPicker("Display", selection: colorBinding(\.screen))
                        ColorPicker("Visier", selection: colorBinding(\.visor))
                        ColorPicker("Stoff", selection: colorBinding(\.fabric))
                    }
                    Picker("Oberfläche", selection: $model.look.finish) {
                        ForEach(Finish.allCases) { Text($0.rawValue).tag($0) }
                    }.pickerStyle(.segmented)
                }
                section("Augen") {
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 3), spacing: 4) {
                        ForEach(model.config.expressions, id: \.self) { e in
                            Button(e) { model.look.expression = e }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                                .tint(model.look.expression == e ? .accentColor : .gray)
                        }
                    }
                    HStack {
                        Text("Leuchtkraft")
                        Slider(value: $model.look.glow, in: 0.2...2.5)
                    }
                    Toggle("Ausdruck passend zur Animation", isOn: $model.look.autoExpression)
                }
                section("Brust-Display") {
                    Picker("Motiv", selection: $model.look.display) {
                        ForEach(model.config.displays, id: \.self) { Text($0).tag($0) }
                    }
                    Toggle("Motiv passend zur Animation", isOn: Binding(
                        get: { model.look.autoDisplay ?? true }, set: { model.look.autoDisplay = $0 }))
                }
                section("Animation") {
                    HStack {
                        Toggle("Wiederholen", isOn: $model.loop)
                        Spacer()
                        Button("Grundpose") { model.clip = nil }
                    }
                    ForEach(groups, id: \.self) { g in
                        DisclosureGroup(groupLabel(g)) {
                            ForEach(model.data.doc.animations.filter { $0.group == g }) { a in
                                Button {
                                    model.clip = a
                                    model.playing = true
                                } label: {
                                    HStack {
                                        Text(a.name).fontWeight(model.clip == a ? .bold : .regular)
                                        Spacer()
                                        Text(a.loop ? "Loop" : "\(a.frames) F").font(.caption).foregroundStyle(.secondary)
                                    }
                                }
                                .buttonStyle(.plain)
                                .help(a.description)
                            }
                        }
                    }
                }
                section("Look") {
                    HStack {
                        Button("Laden …") { model.loadLook() }
                        Button("Speichern …") { model.saveLook() }
                        Button("Screenshot …") { model.screenshot() }
                    }
                }
            }
            .padding(14)
        }
    }

    private var groups: [String] {
        var seen: [String] = []
        for a in model.data.doc.animations where !seen.contains(a.group) { seen.append(a.group) }
        return seen
    }

    private func groupLabel(_ g: String) -> String { model.config.groupLabels?[g] ?? g }

    private func variantPicker(_ slot: SlotInfo) -> some View {
        Picker(slot.label, selection: Binding(
            get: { model.look.variants[slot.id] ?? slot.variants.first ?? "None" },
            set: { model.look.variants[slot.id] = $0; model.look.name = "Eigener" })) {
            ForEach(slot.variants, id: \.self) { v in Text(v == "None" ? "Keins" : v).tag(v) }
        }
    }

    private func section<C: View>(_ title: String, @ViewBuilder _ content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(title.uppercased()).font(.caption.bold()).foregroundStyle(.secondary)
            content()
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor).opacity(0.6), in: RoundedRectangle(cornerRadius: 8))
    }
}
