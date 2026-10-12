import AppKit
import Foundation
import SwiftUI

/// Linear RGB colour (the palette values from Blender are linear).
struct RGB: Codable, Equatable {
    var r: Double
    var g: Double
    var b: Double

    init(_ r: Double, _ g: Double, _ b: Double) { self.r = r; self.g = g; self.b = b }
    init(_ a: [Double]) { self.init(a[0], a[1], a[2]) }

    var luminance: Double { 0.2126 * r + 0.7152 * g + 0.0722 * b }
    var clamped: RGB { RGB(min(max(r, 0), 1), min(max(g, 0), 1), min(max(b, 0), 1)) }

    /// SwiftUI colour in sRGB (for colour pickers).
    var color: Color {
        Color(.sRGB, red: RGB.toSRGB(r), green: RGB.toSRGB(g), blue: RGB.toSRGB(b), opacity: 1)
    }

    init(color: Color) {
        let ns = NSColor(color).usingColorSpace(.sRGB) ?? .white
        self.init(RGB.toLinear(Double(ns.redComponent)), RGB.toLinear(Double(ns.greenComponent)),
                  RGB.toLinear(Double(ns.blueComponent)))
    }

    static func toSRGB(_ c: Double) -> Double {
        let v = max(0, min(1, c))
        return v <= 0.0031308 ? v * 12.92 : 1.055 * pow(v, 1 / 2.4) - 0.055
    }

    static func toLinear(_ c: Double) -> Double {
        c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4)
    }
}

enum Finish: String, Codable, CaseIterable, Identifiable {
    case standard = "Standard", matte = "Matt", satin = "Satin", gloss = "Glanz", candy = "Candy", chrome = "Chrom"
    var id: String { rawValue }
    /// x: roughness factor, y: metalness offset (armour only)
    var params: (Double, Double) {
        switch self {
        case .standard: return (1.0, 0.0)
        case .matte: return (1.9, -0.25)
        case .satin: return (1.35, 0.05)
        case .candy: return (0.35, 0.08)
        case .gloss: return (0.45, 0.0)
        case .chrome: return (0.30, 0.32)
        }
    }

    /// Parameters from robot_config.json > finishes (generator 1.10+), else the built-in values.
    func params(_ config: RobotConfig) -> (Double, Double) {
        if let f = config.finishes?.first(where: { $0.id == rawValue }) { return (f.roughness, f.metalOffset) }
        return params
    }
}

/// A badge on the armour (version 3): ID from robot_config.json > badges, colour linear RGB.
struct BadgeLook: Codable, Equatable {
    var id: String
    var color: RGB
}

/// Glow lines on the armour (version 3): pattern "None" or an ID from robot_config.json > linePatterns.
struct LineLook: Codable, Equatable {
    var pattern: String = "None"
    var color = RGB(1.0, 0.38, 0.05)
    var strength: Double = 1.0
}

/// Everything the player can customise. Saved/loaded as JSON ("Look", schema: robot_look.schema.json).
/// Names of skins, variants, expressions, displays, badges and line patterns are permanent IDs. Older files are
/// migrated on load: version 1 (viewer 1.6, without format/version) and version 2 (1.7-1.9, without skin, badges
/// and lines) take the defaults for missing fields; a version 2 look keeps its colours and gets skin "Custom".
struct Look: Codable, Equatable {
    static let format = "CubeEscapeRobotLook"
    static let currentVersion = 3
    static let maxBadges = 2

    var name: String = "Classic"
    var variants: [String: String] = [:]
    var armor = RGB(0.66, 0.685, 0.72)
    var mech = RGB(0.075, 0.080, 0.092)
    var metal = RGB(0.38, 0.395, 0.42)
    var lights = RGB(1.0, 0.38, 0.05)
    var screen = RGB(0.30, 1.0, 0.62)
    var eyes = RGB(0.02, 0.62, 0.95)
    var visor = RGB(0.006, 0.007, 0.010)
    var fabric = RGB(0.78, 0.10, 0.12)
    var finish: Finish = .standard
    var glow: Double = 1.0
    var expression: String = "Normal"
    var display: String = "Status"
    var autoExpression: Bool = true
    var autoDisplay: Bool? = true
    var skin: String = "Classic"
    var armor2 = RGB(0.66, 0.685, 0.72)
    var zones2: [String] = []
    var badges: [BadgeLook] = []
    var lines = LineLook()

    enum CodingKeys: String, CodingKey {
        case format, version, name, variants, armor, mech, metal, lights, screen, eyes, visor, fabric, finish, glow,
             expression, display, autoExpression, autoDisplay, skin, armor2, zones2, badges, lines
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let f = try c.decodeIfPresent(String.self, forKey: .format), f != Look.format {
            throw DecodingError.dataCorruptedError(forKey: .format, in: c, debugDescription: "kein Roboter-Look: \(f)")
        }
        let version = try c.decodeIfPresent(Int.self, forKey: .version) ?? 1
        if version > Look.currentVersion {
            throw DecodingError.dataCorruptedError(forKey: .version, in: c,
                                                   debugDescription: "Look-Version \(version) ist neuer als dieser Viewer")
        }
        let d = Look()
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? d.name
        variants = try c.decodeIfPresent([String: String].self, forKey: .variants) ?? d.variants
        armor = try c.decodeIfPresent(RGB.self, forKey: .armor) ?? d.armor
        mech = try c.decodeIfPresent(RGB.self, forKey: .mech) ?? d.mech
        metal = try c.decodeIfPresent(RGB.self, forKey: .metal) ?? d.metal
        lights = try c.decodeIfPresent(RGB.self, forKey: .lights) ?? d.lights
        screen = try c.decodeIfPresent(RGB.self, forKey: .screen) ?? d.screen
        eyes = try c.decodeIfPresent(RGB.self, forKey: .eyes) ?? d.eyes
        visor = try c.decodeIfPresent(RGB.self, forKey: .visor) ?? d.visor
        fabric = try c.decodeIfPresent(RGB.self, forKey: .fabric) ?? d.fabric
        let fin = try c.decodeIfPresent(String.self, forKey: .finish) ?? "Standard"
        finish = Finish(rawValue: fin) ?? .standard
        glow = try c.decodeIfPresent(Double.self, forKey: .glow) ?? d.glow
        expression = try c.decodeIfPresent(String.self, forKey: .expression) ?? d.expression
        display = try c.decodeIfPresent(String.self, forKey: .display) ?? d.display
        autoExpression = try c.decodeIfPresent(Bool.self, forKey: .autoExpression) ?? true
        autoDisplay = try c.decodeIfPresent(Bool.self, forKey: .autoDisplay) ?? true
        // version 3: before, a look was only colours - it keeps them and no longer claims a skin
        skin = try c.decodeIfPresent(String.self, forKey: .skin) ?? (version < 3 ? "Custom" : d.skin)
        armor2 = try c.decodeIfPresent(RGB.self, forKey: .armor2) ?? armor
        zones2 = try c.decodeIfPresent([String].self, forKey: .zones2) ?? []
        badges = try c.decodeIfPresent([BadgeLook].self, forKey: .badges) ?? []
        lines = try c.decodeIfPresent(LineLook.self, forKey: .lines) ?? LineLook()
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(Look.format, forKey: .format)
        try c.encode(Look.currentVersion, forKey: .version)
        try c.encode(name, forKey: .name)
        try c.encode(variants, forKey: .variants)
        for (k, v) in [(CodingKeys.armor, armor), (.mech, mech), (.metal, metal), (.lights, lights), (.screen, screen),
                       (.eyes, eyes), (.visor, visor), (.fabric, fabric)] {
            try c.encode(v, forKey: k)
        }
        try c.encode(finish, forKey: .finish)
        try c.encode(glow, forKey: .glow)
        try c.encode(expression, forKey: .expression)
        try c.encode(display, forKey: .display)
        try c.encode(autoExpression, forKey: .autoExpression)
        try c.encode(autoDisplay ?? true, forKey: .autoDisplay)
        try c.encode(skin, forKey: .skin)
        try c.encode(armor2, forKey: .armor2)
        try c.encode(zones2, forKey: .zones2)
        try c.encode(badges, forKey: .badges)
        try c.encode(lines, forKey: .lines)
    }

    /// Replaces unknown IDs with defaults (e.g. a look saved by a newer generator) and clamps values.
    func sanitized(config: RobotConfig) -> (Look, [String]) {
        var l = self
        var warnings: [String] = []
        var fixed: [String: String] = [:]
        for slot in config.slots {
            let fallback = slot.variants.first ?? "None"
            let v = variants[slot.id] ?? fallback
            if slot.variants.contains(v) {
                fixed[slot.id] = v
            } else {
                fixed[slot.id] = fallback
                warnings.append("\(slot.label): „\(v)“ unbekannt")
            }
        }
        for k in variants.keys where !config.slots.contains(where: { $0.id == k }) {
            warnings.append("Bauteil-Slot „\(k)“ unbekannt")
        }
        l.variants = fixed
        if !config.expressions.contains(expression) {
            warnings.append("Augen „\(expression)“ unbekannt")
            l.expression = "Normal"
        }
        if !config.allDisplays.contains(display) {
            warnings.append("Display „\(display)“ unbekannt")
            l.display = "Status"
        }
        if skin != "Custom" && !config.skins.contains(where: { $0.name == skin }) {
            warnings.append("Skin „\(skin)“ unbekannt")
            l.skin = "Custom"
        }
        let zoneIDs = Set((config.zones ?? []).map(\.id))
        for z in zones2 where !zoneIDs.contains(z) { warnings.append("Lackzone „\(z)“ unbekannt") }
        var seen = Set<String>()
        l.zones2 = zones2.filter { zoneIDs.contains($0) && seen.insert($0).inserted }
        l.armor2 = armor2.clamped
        let known = Set((config.badges ?? []).map(\.id))
        for b in badges where !known.contains(b.id) { warnings.append("Abzeichen „\(b.id)“ unbekannt") }
        l.badges = Array(badges.filter { known.contains($0.id) }.prefix(Look.maxBadges))
        if lines.pattern != "None" && !(config.linePatterns ?? []).contains(where: { $0.id == lines.pattern }) {
            warnings.append("Leuchtlinien „\(lines.pattern)“ unbekannt")
            l.lines.pattern = "None"
        }
        l.lines.strength = min(max(lines.strength, 0), 2)
        l.glow = min(max(glow, 0.2), 2.5)
        for kp in [\Look.armor, \.mech, \.metal, \.lights, \.screen, \.eyes, \.visor, \.fabric] {
            l[keyPath: kp] = l[keyPath: kp].clamped
        }
        l.lines.color = l.lines.color.clamped
        for i in l.badges.indices { l.badges[i].color = l.badges[i].color.clamped }
        return (l, warnings)
    }

    /// Takes everything a skin defines: colours, finish, badges, glow lines; with parts also its variants,
    /// expression and display (e.g. Retro: boxy head, antenna, pixel eyes).
    mutating func apply(skin s: Skin, parts: Bool = false) {
        armor = RGB(s.armor); mech = RGB(s.mech); metal = RGB(s.metal); lights = RGB(s.accent)
        screen = RGB(s.screen); eyes = RGB(s.eye); visor = RGB(s.visor); fabric = RGB(s.fabric)
        skin = s.name
        armor2 = RGB(s.armor2 ?? s.armor)
        zones2 = s.zones2 ?? []
        finish = Finish(rawValue: s.finish ?? "Standard") ?? .standard
        badges = (s.badges ?? []).map { BadgeLook(id: $0.id, color: RGB($0.color)) }
        if let ln = s.lines {
            lines = LineLook(pattern: ln.pattern, color: RGB(ln.color), strength: ln.strength)
        } else {
            lines = LineLook(pattern: "None", color: RGB(s.accent), strength: 1.0)
        }
        if parts {
            for (slot, v) in s.parts ?? [:] { variants[slot] = v }
            if let e = s.expression { expression = e }
            if let d = s.display { display = d }
        }
    }

    mutating func apply(type: RobotType, config: RobotConfig) {
        name = type.name
        variants = Dictionary(uniqueKeysWithValues: config.slots.map { ($0.id, $0.variants.first ?? "None") })
        for (k, v) in type.variants { variants[k] = v }
        if let skin = config.skins.first(where: { $0.name == type.skin }) { apply(skin: skin) }
        expression = type.expression
        display = type.display
    }
}
