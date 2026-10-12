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
        case .candy: return (0.35, 0.35)
        case .gloss: return (0.45, 0.0)
        case .chrome: return (0.22, 0.6)
        }
    }
}

/// Everything the player can customise. Saved/loaded as JSON ("Look", schema: robot_look.schema.json).
/// Names of skins, variants, expressions and displays are permanent IDs. Version 1 files (viewer 1.6, without
/// format/version) are migrated on load: missing fields take their defaults.
struct Look: Codable, Equatable {
    static let format = "CubeEscapeRobotLook"
    static let currentVersion = 2

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

    enum CodingKeys: String, CodingKey {
        case format, version, name, variants, armor, mech, metal, lights, screen, eyes, visor, fabric, finish, glow,
             expression, display, autoExpression, autoDisplay
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
        if !config.displays.contains(display) {
            warnings.append("Display „\(display)“ unbekannt")
            l.display = "Status"
        }
        l.glow = min(max(glow, 0.2), 2.5)
        for kp in [\Look.armor, \.mech, \.metal, \.lights, \.screen, \.eyes, \.visor, \.fabric] {
            let c = l[keyPath: kp]
            l[keyPath: kp] = RGB(min(max(c.r, 0), 1), min(max(c.g, 0), 1), min(max(c.b, 0), 1))
        }
        return (l, warnings)
    }

    mutating func apply(skin: Skin) {
        armor = RGB(skin.armor); mech = RGB(skin.mech); metal = RGB(skin.metal); lights = RGB(skin.accent)
        screen = RGB(skin.screen); eyes = RGB(skin.eye); visor = RGB(skin.visor); fabric = RGB(skin.fabric)
    }

    mutating func apply(type: RobotType, config: RobotConfig) {
        name = type.name
        variants = Dictionary(uniqueKeysWithValues: config.slots.map { ($0.id, $0.variants.first ?? "None") })
        for (k, v) in type.variants { variants[k] = v }
        if let skin = config.skins.first(where: { $0.name == type.skin }) { apply(skin: skin) }
        expression = type.expression
        display = type.display
        finish = .standard
    }
}
