import AVFoundation
import AppKit
import ImageIO
import UniformTypeIdentifiers

/// Renders a clip offscreen frame by frame and writes it as an animated GIF or an H.264 video (MP4).
/// Used by the GUI (Export buttons) and the command line (--gif / --video, see Main.swift).
enum ClipExport {
    enum Format { case gif, mp4 }

    /// Offscreen frames of a clip: every `step`-th frame (30 fps clips), one full cycle.
    static func frames(robot: RobotScene, anim: AnimInfo, size: CGSize, step: Int) -> [CGImage] {
        robot.play(anim)
        robot.setPlayback(playing: false, speed: 1, loop: anim.loop, turntable: false)
        var out: [CGImage] = []
        let total = max(anim.frames, 1)
        for f in stride(from: 0, to: total + (anim.loop ? 0 : 1), by: max(step, 1)) {
            robot.seek(fraction: Double(f) / Double(total))
            if let cg = robot.snapshot(size: size).cgImage(forProposedRect: nil, context: nil, hints: nil) {
                out.append(cg)
            }
        }
        return out
    }

    static func write(_ frames: [CGImage], to url: URL, format: Format, fps: Double) throws {
        switch format {
        case .gif: try writeGIF(frames, to: url, fps: fps)
        case .mp4: try writeMP4(frames, to: url, fps: fps)
        }
    }

    static func writeGIF(_ frames: [CGImage], to url: URL, fps: Double) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.gif.identifier as CFString,
                                                         frames.count, nil) else { throw ExportError.create(url) }
        let fileProps = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary
        CGImageDestinationSetProperties(dest, fileProps)
        let frameProps = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: 1.0 / fps]] as CFDictionary
        for f in frames { CGImageDestinationAddImage(dest, f, frameProps) }
        guard CGImageDestinationFinalize(dest) else { throw ExportError.write(url) }
    }

    static func writeMP4(_ frames: [CGImage], to url: URL, fps: Double) throws {
        guard let first = frames.first else { throw ExportError.empty }
        try? FileManager.default.removeItem(at: url)
        let w = first.width & ~1, h = first.height & ~1        // H.264 wants even sizes
        let writer = try AVAssetWriter(outputURL: url, fileType: .mp4)
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: AVVideoCodecType.h264, AVVideoWidthKey: w, AVVideoHeightKey: h])
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32ARGB,
            kCVPixelBufferWidthKey as String: w, kCVPixelBufferHeightKey as String: h])
        writer.add(input)
        guard writer.startWriting() else { throw writer.error ?? ExportError.write(url) }
        writer.startSession(atSourceTime: .zero)
        let scale = CMTimeScale(600)
        for (i, frame) in frames.enumerated() {
            while !input.isReadyForMoreMediaData { Thread.sleep(forTimeInterval: 0.005) }
            guard let pool = adaptor.pixelBufferPool else { throw ExportError.write(url) }
            var buffer: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, pool, &buffer)
            guard let pb = buffer else { throw ExportError.write(url) }
            CVPixelBufferLockBaseAddress(pb, [])
            if let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pb), width: w, height: h, bitsPerComponent: 8,
                                   bytesPerRow: CVPixelBufferGetBytesPerRow(pb), space: CGColorSpaceCreateDeviceRGB(),
                                   bitmapInfo: CGImageAlphaInfo.noneSkipFirst.rawValue) {
                ctx.draw(frame, in: CGRect(x: 0, y: 0, width: w, height: h))
            }
            CVPixelBufferUnlockBaseAddress(pb, [])
            adaptor.append(pb, withPresentationTime: CMTime(value: CMTimeValue(Double(i) * Double(scale) / fps),
                                                            timescale: scale))
        }
        input.markAsFinished()
        let done = DispatchSemaphore(value: 0)
        writer.finishWriting { done.signal() }
        done.wait()
        if writer.status != .completed { throw writer.error ?? ExportError.write(url) }
    }

    enum ExportError: LocalizedError {
        case create(URL), write(URL), empty
        var errorDescription: String? {
            switch self {
            case .create(let u): return "kann \(u.lastPathComponent) nicht anlegen"
            case .write(let u): return "kann \(u.lastPathComponent) nicht schreiben"
            case .empty: return "keine Bilder"
            }
        }
    }
}
