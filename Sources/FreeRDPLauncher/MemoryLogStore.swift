// SPDX-License-Identifier: Apache-2.0
import AppKit
import Foundation

// Both limits apply to retained runtime logs. Nothing in this file writes to disk.
enum LogLevel {
    case fatal, error, warning, info, debug, trace, other
    init(line: String) {
        if line.contains("[FATAL]") { self = .fatal }
        else if line.contains("[ERROR]") { self = .error }
        else if line.contains("[WARN]") || line.contains("[WARNING]") { self = .warning }
        else if line.contains("[INFO]") { self = .info }
        else if line.contains("[DEBUG]") { self = .debug }
        else if line.contains("[TRACE]") { self = .trace }
        else { self = .other }
    }
    var color: NSColor {
        switch self {
        case .fatal, .error: return .systemRed
        case .warning: return .systemOrange
        case .info: return .systemBlue
        case .debug: return .secondaryLabelColor
        case .trace: return .systemPurple
        case .other: return .labelColor
        }
    }
}

struct LogEntry {
    let id: Int
    let text: String
    let level: LogLevel
    var bytes: Int { text.utf8.count }
}

@MainActor
final class MemoryLogStore: ObservableObject {
    @Published private(set) var entries: [LogEntry] = []
    @Published var autoScroll = true
    private(set) var retainedBytes = 0
    private var sequence = 0
    private let maxBytes: Int
    private let maxEntries: Int

    init(maxBytes: Int, maxEntries: Int) {
        self.maxBytes = maxBytes
        self.maxEntries = maxEntries
    }

    func append(_ lines: [String]) {
        guard !lines.isEmpty else { return }
        var updated = entries
        for line in lines {
            sequence += 1
            let entry = LogEntry(id: sequence, text: line, level: LogLevel(line: line))
            guard entry.bytes <= maxBytes else { continue }
            updated.append(entry)
            retainedBytes += entry.bytes
        }
        var removed = 0
        while updated.count - removed > maxEntries || retainedBytes > maxBytes {
            retainedBytes -= updated[removed].bytes
            removed += 1
        }
        if removed > 0 { updated.removeFirst(removed) }
        entries = updated
    }

    func clear() {
        retainedBytes = 0
        entries = []
    }
}

// Split only complete UTF-8 lines, redact secrets before stripping terminal escapes,
// and discard oversized lines instead of retaining an unbounded partial message.
struct LogDecoder {
    private var pending = Data()
    private var discarding = false
    private let secret: String
    private let maxLineBytes: Int
    private let truncatedMessage: String
    private let ansi = try! NSRegularExpression(pattern: "\\u001B\\[[0-?]*[ -/]*[@-~]")

    init(secret: String, maxLineBytes: Int, truncatedMessage: String) {
        self.secret = secret
        self.maxLineBytes = maxLineBytes
        self.truncatedMessage = truncatedMessage
    }

    mutating func consume(_ data: Data, final: Bool = false) -> [String] {
        var result: [String] = []
        for byte in data {
            if byte == 10 {
                if !discarding { result.append(render(pending)) }
                pending.removeAll(keepingCapacity: true)
                discarding = false
            } else if !discarding {
                pending.append(byte)
                if pending.count > maxLineBytes {
                    pending.removeAll(keepingCapacity: true)
                    discarding = true
                    result.append("[WARN] " + truncatedMessage)
                }
            }
        }
        if final && !pending.isEmpty && !discarding {
            result.append(render(pending))
            pending.removeAll()
        }
        return result
    }

    private func render(_ data: Data) -> String {
        var value = String(decoding: data, as: UTF8.self)
        if !secret.isEmpty { value = value.replacingOccurrences(of: secret, with: "••••") }
        value = ansi.stringByReplacingMatches(in: value,
            range: NSRange(value.startIndex..., in: value), withTemplate: "")
        return value.trimmingCharacters(in: .newlines)
    }
}
