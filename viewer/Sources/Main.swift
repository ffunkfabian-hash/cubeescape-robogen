import AppKit
import SwiftUI

/// Entry point. GUI by default; `--snapshot <out.png>` renders offscreen and exits (used by the QA scripts):
///   RobotViewer --snapshot out.png [--type Party] [--look file.json] [--skin Neon] [--expression Love]
///               [--display Heart] [--variant Head=TV ...] [--anim DanceRobot --frame 30] [--view Iso|Front|Side|Back|Face]
///               [--size 1000x1000] [--no-floor] [--no-props] [--save-look out.json] [--data <ViewerData dir>]
///               [--gif clip.gif | --video clip.mp4]   (with --anim: the whole clip instead of one frame)
@main
enum Main {
    static func main() {
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--snapshot"), i + 1 < args.count {
            exit(Snapshot.run(out: args[i + 1], args: args))
        }
        RobotViewerApp.main()
    }
}

struct RobotViewerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) var appDelegate

    var body: some Scene {
        WindowGroup("Cube Escape – Robot Viewer") {
            if let url = RobotData.locate(), let data = try? RobotData(root: url) {
                ContentView(model: ViewerModel(data: data))
                    .frame(minWidth: 1100, minHeight: 720)
            } else {
                Text("ViewerData nicht gefunden. Erwartet in RobotViewer.app/Contents/Resources/ViewerData.")
                    .padding(40)
            }
        }
        .windowStyle(.titleBar)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

enum Snapshot {
    static func value(_ args: [String], _ key: String) -> String? {
        guard let i = args.firstIndex(of: key), i + 1 < args.count else { return nil }
        return args[i + 1]
    }

    static func run(out: String, args: [String]) -> Int32 {
        guard let url = RobotData.locate() else {
            FileHandle.standardError.write("ViewerData not found\n".data(using: .utf8)!)
            return 2
        }
        do {
            let data = try RobotData(root: url)
            let robot = RobotScene(data: data)
            var look = Look()
            let typeName = value(args, "--type") ?? "Classic"
            if let t = data.config.robotTypes.first(where: { $0.name == typeName }) { look.apply(type: t, config: data.config) }
            if let path = value(args, "--look") {
                let raw = try JSONDecoder().decode(Look.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
                let (clean, warnings) = raw.sanitized(config: data.config)
                look = clean
                for w in warnings { print("look warning: \(w)") }
            }
            if let out = value(args, "--save-look") {          // round trip test: writes the (migrated) look
                let enc = JSONEncoder()
                enc.outputFormatting = [.prettyPrinted, .sortedKeys]
                try enc.encode(look).write(to: URL(fileURLWithPath: out))
            }
            if let s = value(args, "--skin"), let skin = data.config.skins.first(where: { $0.name == s }) {
                look.apply(skin: skin, parts: args.contains("--skin-parts"))
            }
            if let e = value(args, "--expression") { look.expression = e }
            if let d = value(args, "--display") { look.display = d }
            if let f = value(args, "--finish"), let fin = Finish(rawValue: f) { look.finish = fin }
            for (i, a) in args.enumerated() where a == "--variant" && i + 1 < args.count {
                let kv = args[i + 1].split(separator: "=").map(String.init)
                if kv.count == 2 { look.variants[kv[0]] = kv[1] }
            }
            look.autoExpression = args.contains("--auto-expression")
            robot.apply(look: look)
            let dims = (value(args, "--size") ?? "1000x1000").split(separator: "x").compactMap { Double($0) }
            let size = CGSize(width: dims.first ?? 1000, height: dims.last ?? 1000)
            if let name = value(args, "--anim"), let anim = data.doc.animations.first(where: { $0.name == name }),
               let movie = value(args, "--gif") ?? value(args, "--video") {
                robot.setView(value(args, "--view") ?? "Iso")
                robot.showFloor = !args.contains("--no-floor")
                robot.showProps = !args.contains("--no-props")
                let gif = value(args, "--gif") != nil
                let frames = ClipExport.frames(robot: robot, anim: anim, size: size, step: gif ? 2 : 1)
                try ClipExport.write(frames, to: URL(fileURLWithPath: movie), format: gif ? .gif : .mp4,
                                     fps: gif ? 15 : 30)
                print("\(gif ? "gif" : "video") \(movie) (\(frames.count) frames)")
                return 0
            }
            if let name = value(args, "--anim"), let anim = data.doc.animations.first(where: { $0.name == name }) {
                robot.play(anim)
                robot.setPlayback(playing: false, speed: 1, loop: anim.loop, turntable: false)
                let frame = Double(value(args, "--frame") ?? "0") ?? 0
                robot.seek(fraction: frame / Double(max(anim.frames, 1)))
            }
            robot.setView(value(args, "--view") ?? "Iso")
            robot.showFloor = !args.contains("--no-floor")
            robot.showProps = !args.contains("--no-props")
            let img = robot.snapshot(size: size)
            try img.pngData()?.write(to: URL(fileURLWithPath: out))
            print("snapshot \(out)")
            return 0
        } catch {
            FileHandle.standardError.write("snapshot failed: \(error)\n".data(using: .utf8)!)
            return 1
        }
    }
}
