// SPDX-License-Identifier: Apache-2.0
import AppKit
import SwiftUI

@main
struct LogViewChecks {
    @MainActor static func main() {
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 300, height: 120))
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        let text = NSTextView(frame: NSRect(x: 0, y: 0, width: 1200, height: 3000))
        text.isHorizontallyResizable = true
        text.isVerticallyResizable = true
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        text.textContainer?.widthTracksTextView = false
        text.textContainer?.heightTracksTextView = false
        text.textContainer?.containerSize = text.maxSize
        scroll.documentView = text
        let entries = (0..<200).map { LogEntry(id: $0, text: String(repeating: "long log line ", count: 30), level: .info) }
        let state = LogTextView.Coordinator()
        let paused = LogTextView(entries: entries, autoScroll: false)
        paused.update(scroll, coordinator: state, scheme: .light)
        scroll.contentView.scroll(to: NSPoint(x: 50, y: 0))
        let before = scroll.contentView.bounds.origin
        paused.update(scroll, coordinator: state, scheme: .light)
        precondition(scroll.contentView.bounds.origin == before)
        LogTextView(entries: entries, autoScroll: true).update(scroll, coordinator: state, scheme: .light)
        precondition(scroll.contentView.bounds.origin.y > before.y)
        precondition(scroll.contentView.bounds.origin.x == before.x)
        precondition(text.string.components(separatedBy: "\n").count == entries.count + 1)
        paused.update(scroll, coordinator: state, scheme: .light)
        scroll.contentView.scroll(to: before)
        LogTextView(entries: entries, autoScroll: true).update(scroll, coordinator: state, scheme: .light)
        precondition(scroll.contentView.bounds.origin.y > before.y)
        print("PASS: follow output scrolls immediately without new lines, preserves horizontal position and does not duplicate text")
    }
}
