import Foundation
import Vision
import AppKit

let args = CommandLine.arguments
guard args.count == 3 else { fatalError("usage: ocr_images INPUT_DIR OUTPUT_DIR") }
let input = URL(fileURLWithPath: args[1])
let output = URL(fileURLWithPath: args[2])
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let files = try FileManager.default.contentsOfDirectory(at: input, includingPropertiesForKeys: nil)
    .filter { ["jpg", "jpeg", "png", "webp"].contains($0.pathExtension.lowercased()) }.sorted { $0.path < $1.path }
for (index, file) in files.enumerated() {
    let destination = output.appendingPathComponent(file.lastPathComponent + ".json")
    if FileManager.default.fileExists(atPath: destination.path) { continue }
    autoreleasepool {
        do {
            let request = VNRecognizeTextRequest()
            request.recognitionLevel = .accurate
            request.recognitionLanguages = ["zh-Hans", "en-US"]
            request.usesLanguageCorrection = true
            let handler = VNImageRequestHandler(url: file, options: [:])
            try handler.perform([request])
            let lines: [[String: Any]] = (request.results ?? []).compactMap { observation in
                guard let candidate = observation.topCandidates(1).first else { return nil }
                let b = observation.boundingBox
                return ["text": candidate.string, "confidence": candidate.confidence,
                        "box": [b.origin.x, b.origin.y, b.width, b.height]]
            }
            let result: [String: Any] = ["file": file.lastPathComponent, "engine": "Apple Vision",
                                       "status": "ok", "lines": lines,
                                       "text": lines.compactMap { $0["text"] as? String }.joined(separator: "\n")]
            let data = try JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: destination, options: .atomic)
        } catch {
            print("FAILED \(file.lastPathComponent): \(error)")
        }
    }
    if index % 20 == 0 { print("OCR \(index + 1)/\(files.count)"); fflush(stdout) }
}
print("OCR finished: \(files.count) images")
