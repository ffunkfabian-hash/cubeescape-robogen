import Foundation
import simd

// Data written by scripts/export_viewer.py (Blender). Coordinates are already SceneKit (Y up, robot looks +Z).

struct RobotDoc: Decodable {
    let format: Int
    let fps: Double
    let floatsPerBoneFrame: Int
    let vertexStride: Int
    let bones: [BoneInfo]
    let parts: [PartInfo]
    let animations: [AnimInfo]
    let config: RobotConfig
    let textures: [String]
}

struct BoneInfo: Decodable {
    let name: String
    let parent: String?
    let rest: [Float]          // row-major 4x4, parent space

    var restMatrix: simd_float4x4 {
        simd_float4x4(rows: [
            SIMD4(rest[0], rest[1], rest[2], rest[3]),
            SIMD4(rest[4], rest[5], rest[6], rest[7]),
            SIMD4(rest[8], rest[9], rest[10], rest[11]),
            SIMD4(rest[12], rest[13], rest[14], rest[15]),
        ])
    }
}

struct PartInfo: Decodable {
    let object: String
    let form: String?          // body form of the chunk (1.14), "" or missing = base geometry
    let slot: String
    let variant: String
    let bone: String
    let material: String       // body | fabric | eyes | display
    let spin: Bool
    let part: String
    let pivot: [Float]?
    let vertexOffset: Int
    let vertexCount: Int
    let indexOffset: Int
    let indexCount: Int
}

struct AnimInfo: Decodable, Identifiable, Hashable {
    let name: String
    let group: String
    let frames: Int
    let loop: Bool
    let description: String
    let unity: String
    let offset: Int
    var id: String { name }
}

struct SlotInfo: Decodable, Identifiable {
    let id: String
    let label: String
    let variants: [String]
    let accessory: Bool
}

struct RobotType: Decodable, Identifiable {
    let name: String
    let variants: [String: String]
    let skin: String
    let expression: String
    let display: String
    var id: String { name }
}

struct Skin: Decodable, Identifiable {
    struct Badge: Decodable { let id: String; let color: [Double] }
    struct Lines: Decodable { let pattern: String; let color: [Double]; let strength: Double }
    let name: String
    let armor: [Double]
    let mech: [Double]
    let metal: [Double]
    let eye: [Double]
    let screen: [Double]
    let accent: [Double]
    let visor: [Double]
    let fabric: [Double]
    // since generator 1.10 (optional so older ViewerData still loads)
    let armor2: [Double]?
    let zones2: [String]?
    struct Material: Decodable { let id: String; let color: [Double] }
    struct Layer: Decodable { let id: String; let amount: Double; let color: [Double] }
    let material: Material?
    let layers: [Layer]?
    let wear: Double?
    let finish: String?
    let badges: [Badge]?
    let lines: Lines?
    // since generator 1.13
    struct Special: Decodable { let id: String; let color: [Double]; let color2: [Double]; let strength: Double
                                let speed: Double }
    let special: Special?
    // since generator 1.14: body form ("None" or an ID of RobotConfig.forms)
    let form: String?
    let parts: [String: String]?
    let expression: String?
    let display: String?
    let theme: String?
    let stage: String?
    let startSet: Bool?
    var id: String { name }
}

/// A channel of a mask texture (badges, glow line patterns).
struct MaskChannel: Decodable, Identifiable {
    let id: String
    let texture: String
    let channel: Int
}

/// A surface layer or material (1.11): mask channel plus default colour (linear) and surface.
struct LayerInfo: Decodable, Identifiable {
    let id: String
    let texture: String
    let channel: Int
    let color: [Double]?
    let roughness: Double?
    let metalOffset: Double?
    let base: [Double]?         // materials: a fitting armour colour
}

struct WeatheringInfo: Decodable {
    let coverSoft: Double
    let wearDefault: Double
    let bareMetal: [Double]
}

struct FinishInfo: Decodable {
    let id: String
    let roughness: Double
    let metalOffset: Double
}

struct ExpressionStyle: Decodable {
    let tint: [Double]?
    let glow: Double?
}

struct BlinkInfo: Decodable {
    let expression: String
    let interval: [Double]
    let duration: Double
    let during: [String]
}

struct DisplayAnimation: Decodable {
    let name: String
    let row: Int
    let frames: Int
    let fps: Double
}

/// Special material (1.13): defaults and the description for the later URP material.
struct SpecialInfo: Decodable, Identifiable {
    let id: String
    let label: String
    let description: String
    let urp: String
    let color: [Double]
    let color2: [Double]
    let strength: Double
    let speed: Double
}

/// Body form (1.14): "mesh" = own geometry chunks, "shader" = cut-out in the body shader, "parts" = extra parts.
struct FormInfo: Decodable, Identifiable {
    let id: String
    let kind: String
    let label: String
    let description: String
    let urp: String
}

/// Holes of the form "Perforated": grid in UV space (Blender/Unity convention, v up).
struct PerforationInfo: Decodable {
    let spacing: Double
    let radius: Double
    let cells: Double?
}

/// Particle emitter of an accessory (1.12): position/direction in character space (right, up, forward).
struct EmitterInfo: Decodable {
    let id: String
    let kind: String
    let slot: String
    let variant: String
    let bone: String
    let position: [Float]
    let direction: [Float]

    /// Character space -> SceneKit (robot looks +Z, its right is -X).
    static func scn(_ v: [Float]) -> SIMD3<Float> { SIMD3(-v[0], v[1], v[2]) }
}

struct FlipbookInfo: Decodable {
    let texture: String
    let grid: [Int]
    let cell: [Double]          // UV size of one frame
}

/// `[frame, value]` pair from the generator's clip data.
struct FrameKey<V: Decodable>: Decodable {
    let frame: Double
    let value: V

    init(from decoder: Decoder) throws {
        var c = try decoder.unkeyedContainer()
        frame = try c.decode(Double.self)
        value = try c.decode(V.self)
    }
}

struct RootMotion: Decodable {
    let move: [Double]          // [right, up, forward] in m
    let turnRight: Double       // degrees
}

struct HeightRange: Decodable {
    let min: Double
    let max: Double
}

/// Per-clip game data (robot_config.json > clips): everything besides the bone curves.
struct ClipMeta: Decodable {
    let hideHeldItem: Bool
    let expression: [FrameKey<String>]
    let display: [FrameKey<String>]
    let emission: [FrameKey<Double>]
    let events: [FrameKey<String>]
    let footsteps: [FrameKey<String>]
    let prop: PropInfo?
    let rootMotion: RootMotion
    let height: HeightRange
    let naturalSpeed: Double
    let hands: [String: [[Double]]]?    // L/R knuckle per frame, [right, up, forward]

    /// Name valid at a frame ("$base" = the look's own choice).
    static func step(_ keys: [FrameKey<String>], _ frame: Double, base: String) -> String {
        guard var v = keys.first?.value else { return base }
        for k in keys where k.frame <= frame { v = k.value }
        return v == "$base" ? base : v
    }

    /// Emission factor at a frame (linear between keys, 1 without keys).
    func power(at frame: Double) -> Double {
        guard let first = emission.first else { return 1 }
        if frame <= first.frame { return first.value }
        for (a, b) in zip(emission, emission.dropFirst()) where frame <= b.frame {
            let t = (frame - a.frame) / max(b.frame - a.frame, 1e-6)
            return a.value + (b.value - a.value) * t
        }
        return emission.last?.value ?? 1
    }
}

/// Helper geometry for the viewer (character space, m): ledge (top, edge), bar (up, forward),
/// ladder (forward = rung plane, rail = half width, spacing), box (forward = front face, top = height).
struct PropInfo: Decodable {
    let type: String
    let top: Double?
    let edge: Double?
    let up: Double?
    let forward: Double?
    let rail: Double?
    let spacing: Double?
}

struct TypeBounds: Decodable {
    let width: Double
    let coreWidth: Double
    let depth: Double
    let height: Double
}

struct RobotConfig: Decodable {
    let slots: [SlotInfo]
    let robotTypes: [RobotType]
    let skins: [Skin]
    let expressions: [String]
    let displays: [String]
    let eyeGrid: [Int]
    let displayGrid: [Int]
    let displayCellHeight: Double
    let spinParts: [String]
    let lookVersion: Int?
    let groupLabels: [String: String]?
    let clips: [String: ClipMeta]?
    let typeBounds: [String: TypeBounds]?
    // since generator 1.10
    let finishes: [FinishInfo]?
    let badges: [MaskChannel]?
    let linePatterns: [MaskChannel]?
    let zones: [MaskChannel]?
    // since generator 1.11
    let layers: [LayerInfo]?
    let materials: [LayerInfo]?
    let weathering: WeatheringInfo?
    let linePaint: Double?
    let expressionStyles: [String: ExpressionStyle]?
    let blink: BlinkInfo?
    let displayAnimations: [DisplayAnimation]?
    let flipbook: FlipbookInfo?
    // since generator 1.12
    let emitters: [EmitterInfo]?
    // since generator 1.13
    let specials: [SpecialInfo]?
    // since generator 1.14
    let forms: [FormInfo]?
    let perforation: PerforationInfo?

    /// Static motifs and animated ones (flipbook).
    var allDisplays: [String] { displays + (displayAnimations ?? []).map(\.name) }
}

/// Loaded robot: JSON + binary buffers.
final class RobotData {
    let doc: RobotDoc
    let root: URL
    let vertices: Data
    let indices: Data
    let anim: [Float]

    init(root: URL) throws {
        self.root = root
        let json = try Data(contentsOf: root.appendingPathComponent("robot.json"))
        doc = try JSONDecoder().decode(RobotDoc.self, from: json)
        vertices = try Data(contentsOf: root.appendingPathComponent("vertices.bin"))
        indices = try Data(contentsOf: root.appendingPathComponent("indices.bin"))
        let a = try Data(contentsOf: root.appendingPathComponent("anim.bin"))
        anim = a.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
    }

    func texture(_ name: String) -> URL { root.appendingPathComponent("textures").appendingPathComponent(name) }

    var config: RobotConfig { doc.config }

    static func locate() -> URL? {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--data"), i + 1 < args.count {
            return URL(fileURLWithPath: args[i + 1])
        }
        if let res = Bundle.main.resourceURL?.appendingPathComponent("ViewerData"),
           FileManager.default.fileExists(atPath: res.appendingPathComponent("robot.json").path) {
            return res
        }
        let dev = URL(fileURLWithPath: FileManager.default.currentDirectoryPath).appendingPathComponent("output/ViewerData")
        return FileManager.default.fileExists(atPath: dev.path) ? dev : nil
    }
}
