import AppKit
import SceneKit
import SwiftUI
import UniformTypeIdentifiers

/// Clip position for the timeline. Its own object: published every frame on the model, it made SwiftUI rebuild the
/// whole sidebar 60 times a second (the viewer stuttered); only the playback bar observes this one.
@MainActor
final class Playhead: ObservableObject {
    @Published var progress = 0.0
}

@MainActor
final class ViewerModel: ObservableObject {
    let data: RobotData
    let robot: RobotScene
    @Published var look = Look() { didSet { robot.apply(look: look) } }
    @Published var selectedSkin = "Classic"
    @Published var clip: AnimInfo? { didSet { robot.play(clip); robotB?.play(clip) } }
    /// A/B comparison: a second robot with the look remembered as B, same clip and playback.
    @Published var compare = false { didSet { if compare { ensureB() } } }
    @Published var lookB: Look? { didSet { if let l = lookB { robotB?.apply(look: l) } } }
    private(set) var robotB: RobotScene?
    @Published var playing = true { didSet { pushPlayback() } }
    @Published var speed = 1.0 { didSet { pushPlayback() } }
    @Published var loop = true { didSet { pushPlayback() } }
    @Published var turntable = false { didSet { pushPlayback() } }
    @Published var showFloor = true { didSet { robot.showFloor = showFloor } }
    @Published var showProps = true { didSet { robot.showProps = showProps } }
    let playhead = Playhead()
    var progress: Double { playhead.progress }
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
            DispatchQueue.main.async {
                guard let head = self?.playhead, abs(head.progress - p) > 0.002 else { return }
                head.progress = p
            }
        }
    }

    var config: RobotConfig { data.config }

    func pushPlayback() {
        robot.setPlayback(playing: playing, speed: speed, loop: loop, turntable: turntable)
        robotB?.setPlayback(playing: playing, speed: speed, loop: loop, turntable: turntable)
    }

    func ensureB() {
        if lookB == nil { lookB = look }
        if robotB == nil {
            let b = RobotScene(data: data)
            robotB = b
            b.apply(look: lookB ?? look)
            b.play(clip)
            pushPlayback()
        }
    }

    func rememberAsB() {
        lookB = look
        compare = true
        status = "Look als B gemerkt – Vergleich links A (aktuell), rechts B"
    }

    func swapAB() {
        guard let b = lookB else { return }
        lookB = look
        look = b
        selectedSkin = look.skin
    }

    func setView(_ v: String) {
        robot.setView(v)
        robotB?.setView(v)
    }

    func seek(_ f: Double) {
        robot.seek(fraction: f)
        robotB?.seek(fraction: f)
    }

    /// Exports the playing clip as GIF or MP4 (offscreen render of every 2nd frame at 15 fps, or all at 30 fps).
    func export(_ format: ClipExport.Format) {
        guard let anim = clip else { status = "Erst eine Animation wählen"; return }
        let p = NSSavePanel()
        p.allowedContentTypes = [format == .gif ? .gif : .mpeg4Movie]
        p.nameFieldStringValue = "Roboter_\(look.name)_\(anim.name).\(format == .gif ? "gif" : "mp4")"
        guard p.runModal() == .OK, let url = p.url else { return }
        let off = RobotScene(data: data)
        off.apply(look: look)
        off.setView(robot.currentView)
        let step = format == .gif ? 2 : 1
        let size = format == .gif ? CGSize(width: 600, height: 600) : CGSize(width: 1080, height: 1080)
        let frames = ClipExport.frames(robot: off, anim: anim, size: size, step: step)
        do {
            try ClipExport.write(frames, to: url, format: format, fps: 30.0 / Double(step))
            status = "\(frames.count) Bilder exportiert: \(url.lastPathComponent)"
        } catch { status = "Export fehlgeschlagen: \(error.localizedDescription)" }
    }

    func applyType(_ t: RobotType) {
        var l = look
        l.apply(type: t, config: config)
        selectedSkin = t.skin
        look = l
    }

    func applySkin(_ name: String) {
        guard let s = config.skins.first(where: { $0.name == name }) else { return }
        var l = look
        for slot in (currentSkin?.parts ?? [:]).keys {       // parts of the previous skin go back to the default
            l.variants[slot] = config.slots.first { $0.id == slot }?.variants.first ?? "None"
        }
        l.apply(skin: s, parts: true)
        look = l
        selectedSkin = name
    }

    var currentSkin: Skin? { config.skins.first { $0.name == selectedSkin } }

    /// A material also sets its fitting armour colour (wood looks like wood on brown, not on white); "Keiner" restores
    /// the colour from before.
    /// The armour colour before a material replaced it; choosing "Keiner" brings it back.
    private var armorBeforeMaterial: RGB?

    func setMaterial(_ id: String) {
        var l = look
        if let m = (config.materials ?? []).first(where: { $0.id == id }) {
            if l.material.id == "None" { armorBeforeMaterial = l.armor }
            l.material = MaterialLook(id: id, color: RGB(m.color ?? [0.3, 0.2, 0.1]))
            if let base = m.base { l.armor = RGB(base) }
        } else {
            l.material = MaterialLook()
            if let before = armorBeforeMaterial { l.armor = before }
            armorBeforeMaterial = nil
        }
        l.skin = "Custom"
        look = l
    }

    func setLayer(_ info: LayerInfo, amount: Double) {
        var l = look
        if let i = l.layers.firstIndex(where: { $0.id == info.id }) {
            if amount <= 0.001 { l.layers.remove(at: i) } else { l.layers[i].amount = amount }
        } else if amount > 0.001 {
            l.layers.append(LayerLook(id: info.id, amount: amount, color: RGB(info.color ?? [0.5, 0.5, 0.5])))
        }
        l.skin = "Custom"
        look = l
    }

    func setBadge(_ slot: Int, id: String) {
        var l = look
        if id == "None" {
            if slot < l.badges.count { l.badges.remove(at: slot) }
        } else if slot < l.badges.count {
            l.badges[slot].id = id
        } else {
            l.badges.append(BadgeLook(id: id, color: slot == 0 ? RGB(0.9, 0.05, 0.05) : RGB(1, 1, 1)))
        }
        l.skin = "Custom"
        look = l
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
        l.display = config.allDisplays.randomElement() ?? "Status"
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
                HStack(spacing: 2) {
                    SceneContainer(robot: model.robot)
                    if model.compare, let b = model.robotB {
                        SceneContainer(robot: b)
                            .overlay(alignment: .topLeading) {
                                Text("B").font(.title.bold()).padding(10).foregroundStyle(.secondary)
                            }
                    }
                }
                VStack(alignment: .leading, spacing: 6) {
                    HStack(spacing: 6) {
                        ForEach(RobotScene.views, id: \.self) { v in
                            Button(v) { model.setView(v) }
                        }
                        Toggle("Drehteller", isOn: $model.turntable).toggleStyle(.button)
                        Toggle("Boden", isOn: $model.showFloor).toggleStyle(.button)
                        Toggle("Hilfsobjekte", isOn: $model.showProps).toggleStyle(.button)
                            .help("Kante, Stange, Leiter oder Kiste aus den Clip-Daten einblenden")
                    }
                    PlaybackBar(model: model, playhead: model.playhead)
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
                        ForEach(model.config.skins) { s in
                            Text((s.startSet ?? false) ? "\(s.name) ★" : s.name).tag(s.name)
                        }
                    }
                    if let s = model.currentSkin, let theme = s.theme, !theme.isEmpty {
                        Text(theme + (model.look.skin == "Custom" ? " · angepasst" : ""))
                            .font(.caption).foregroundStyle(.secondary)
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
                section("Werkstoff") {
                    Picker("Werkstoff", selection: Binding(get: { model.look.material.id },
                                                           set: { model.setMaterial($0) })) {
                        Text("Keiner").tag("None")
                        ForEach(model.config.materials ?? []) { Text(label($0.id)).tag($0.id) }
                    }
                    if model.look.material.id != "None" {
                        ColorPicker("Musterfarbe", selection: Binding(get: { model.look.material.color.color },
                                                                      set: { model.look.material.color = RGB(color: $0) }))
                    }
                }
                section("Schichten") {
                    ForEach((model.config.layers ?? []).filter { $0.color != nil }) { info in layerRow(info) }
                    HStack {
                        Text("Abnutzung")
                        Slider(value: $model.look.wear, in: 0...1)
                        Text(model.look.wear < 0.25 ? "sauber" : model.look.wear > 0.75 ? "verbeult" : "normal")
                            .font(.caption).frame(width: 56)
                    }
                }
                section("Zweitfarbe") {
                    ColorPicker("Farbe", selection: colorBinding(\.armor2))
                    LazyVGrid(columns: Array(repeating: GridItem(.flexible()), count: 4), spacing: 4) {
                        ForEach(model.config.zones ?? []) { z in
                            Toggle(label(z.id), isOn: Binding(
                                get: { model.look.zones2.contains(z.id) },
                                set: { on in
                                    model.look.zones2.removeAll { $0 == z.id }
                                    if on { model.look.zones2.append(z.id) }
                                    model.look.skin = "Custom"
                                }))
                            .toggleStyle(.button).controlSize(.small)
                        }
                    }
                }
                section("Abzeichen") {
                    ForEach(0..<Look.maxBadges, id: \.self) { i in badgeRow(i) }
                }
                section("Leuchtlinien") {
                    Picker("Muster", selection: Binding(get: { model.look.lines.pattern },
                                                        set: { model.look.lines.pattern = $0; model.look.skin = "Custom" })) {
                        Text("Keine").tag("None")
                        ForEach(model.config.linePatterns ?? []) { Text(label($0.id)).tag($0.id) }
                    }
                    if model.look.lines.pattern != "None" {
                        HStack {
                            ColorPicker("Farbe", selection: Binding(get: { model.look.lines.color.color },
                                                                    set: { model.look.lines.color = RGB(color: $0) }))
                            Text("Stärke")
                            Slider(value: $model.look.lines.strength, in: 0...2)
                        }
                    }
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
                        ForEach((model.config.displayAnimations ?? []).map(\.name), id: \.self) {
                            Text("\($0) (animiert)").tag($0)
                        }
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
                    HStack {
                        Button("GIF …") { model.export(.gif) }
                            .help("Die laufende Animation als GIF (15 fps, 600 px)")
                        Button("Video …") { model.export(.mp4) }
                            .help("Die laufende Animation als MP4 (30 fps, 1080 px)")
                    }
                }
                section("Vergleich A/B") {
                    HStack {
                        Button("Als B merken") { model.rememberAsB() }
                            .help("Den aktuellen Look rechts daneben stellen; danach links weiter ändern")
                        Toggle("Nebeneinander", isOn: $model.compare)
                        Button("Tauschen") { model.swapAB() }.disabled(model.lookB == nil)
                    }
                }
            }
            .padding(14)
        }
    }

    private static let labels = ["HazardStripes": "Warnstreifen", "Cross": "Kreuz", "Cloud": "Wolke",
                                 "Number": "Nummer", "Biohazard": "Biohazard", "Glyph": "Schriftzeichen",
                                 "RacingStripes": "Rennstreifen", "Target": "Zielmarke", "Circuit": "Leiterbahnen",
                                 "Rings": "Ringe", "Seams": "Kanten", "Bolt": "Blitze", "Joints": "Gelenke",
                                 "Head": "Kopf", "Ears": "Ohren", "Chest": "Brust", "Pads": "Schultern",
                                 "Arms": "Arme", "Hips": "Hüfte", "Legs": "Beine", "Feet": "Füße",
                                 "Wood": "Holz", "Stone": "Stein", "Sand": "Sand", "Paper": "Papier",
                                 "Concrete": "Beton", "Brass": "Messing", "Rust": "Rost", "Moss": "Moos",
                                 "Frost": "Frost", "Embers": "Glutrisse", "Camo": "Tarnung"]

    private func label(_ id: String) -> String { Sidebar.labels[id] ?? id }

    // Bindings look the entry up when they are read: SwiftUI may still read a row's binding after the list shrank
    // (a skin with fewer layers or badges, a layer set to 0), and a captured index then traps.
    private func layerRow(_ info: LayerInfo) -> some View {
        let layer = { model.look.layers.first { $0.id == info.id } }
        return HStack {
            Text(label(info.id)).frame(width: 70, alignment: .leading)
            Slider(value: Binding(get: { layer()?.amount ?? 0 },
                                  set: { model.setLayer(info, amount: $0) }), in: 0...1)
            if layer() != nil {
                ColorPicker("", selection: Binding(
                    get: { layer()?.color.color ?? .gray },
                    set: { c in
                        if let i = model.look.layers.firstIndex(where: { $0.id == info.id }) {
                            model.look.layers[i].color = RGB(color: c)
                        }
                    })).labelsHidden()
            }
        }
    }

    private func badgeRow(_ i: Int) -> some View {
        HStack {
            Picker(i == 0 ? "Erstes" : "Zweites", selection: Binding(
                get: { i < model.look.badges.count ? model.look.badges[i].id : "None" },
                set: { model.setBadge(i, id: $0) })) {
                Text("Keins").tag("None")
                ForEach(model.config.badges ?? []) { Text(label($0.id)).tag($0.id) }
            }
            if i < model.look.badges.count {
                ColorPicker("", selection: Binding(
                    get: { i < model.look.badges.count ? model.look.badges[i].color.color : .gray },
                    set: { if i < model.look.badges.count { model.look.badges[i].color = RGB(color: $0) } }))
                    .labelsHidden()
            }
        }
        .disabled(i > model.look.badges.count)
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


/// Play/pause, timeline, tempo and the clip's game data: the only view that follows the clip position every frame.
struct PlaybackBar: View {
    @ObservedObject var model: ViewerModel
    @ObservedObject var playhead: Playhead

    var body: some View {
        if let c = model.clip {
            HStack {
                Button(model.playing ? "Pause" : "Play") { model.playing.toggle() }
                    .keyboardShortcut(.space, modifiers: [])
                Text(c.name).font(.headline)
                Slider(value: Binding(get: { playhead.progress }, set: { model.seek($0) }))
                    .frame(width: 220)
                Text("Tempo")
                Slider(value: $model.speed, in: 0.1...2.0).frame(width: 110)
                Text(String(format: "%.1fx", model.speed)).monospacedDigit()
            }
        }
        let info = model.clipInfo
        if !info.isEmpty { Text(info).font(.caption).monospacedDigit() }
    }
}
