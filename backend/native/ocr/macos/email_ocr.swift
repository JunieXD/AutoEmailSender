import Foundation
import Vision

guard CommandLine.arguments.count == 2 else {
    fputs("usage: email-ocr <image-path>\n", stderr)
    exit(2)
}

let imageURL = URL(fileURLWithPath: CommandLine.arguments[1])
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = false
request.recognitionLanguages = ["en-US"]

do {
    let handler = VNImageRequestHandler(url: imageURL, options: [:])
    try handler.perform([request])
    let observations = (request.results ?? []).sorted { left, right in
        if abs(left.boundingBox.midY - right.boundingBox.midY) > 0.01 {
            return left.boundingBox.midY > right.boundingBox.midY
        }
        return left.boundingBox.minX < right.boundingBox.minX
    }
    for observation in observations {
        if let candidate = observation.topCandidates(1).first {
            print(candidate.string)
        }
    }
} catch {
    fputs("Vision OCR failed: \(error)\n", stderr)
    exit(1)
}
