// SPDX-License-Identifier: Apache-2.0
import AppKit
import SwiftUI

struct LogWindow: View {
    @ObservedObject var model: LauncherModel
    @ObservedObject var logs: MemoryLogStore
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(model.localizer.text("logs.count", logs.entries.count))
                    .foregroundStyle(.secondary)
                Spacer()
                Toggle(model.text("logs.follow"), isOn: $logs.autoScroll)
                Button(model.text("logs.clear")) { logs.clear() }
                    .disabled(logs.entries.isEmpty)
            }.padding(12)
            Divider()
            LogTextView(entries: logs.entries, autoScroll: logs.autoScroll)
            Divider()
            Text(model.text("logs.memory"))
                .font(.caption).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, alignment: .leading).padding(10)
        }
    }
}

struct LogTextView: NSViewRepresentable {
    let entries: [LogEntry]
    let autoScroll: Bool
    @Environment(\.colorScheme) private var colorScheme

    final class Coordinator {
        var firstID: Int?
        var lastID: Int?
        var scheme: ColorScheme?
        var autoScroll = false
    }
    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSScrollView()
        scroll.hasVerticalScroller = true
        scroll.hasHorizontalScroller = true
        scroll.autohidesScrollers = false
        scroll.borderType = .noBorder
        let text = NSTextView(frame: .zero)
        text.isEditable = false
        text.isSelectable = true
        text.isRichText = false
        text.isAutomaticLinkDetectionEnabled = false
        text.font = .monospacedSystemFont(ofSize: NSFont.smallSystemFontSize, weight: .regular)
        text.backgroundColor = .textBackgroundColor
        text.textColor = .labelColor
        text.textContainerInset = NSSize(width: 10, height: 10)
        text.isHorizontallyResizable = true
        text.isVerticallyResizable = true
        text.autoresizingMask = []
        text.minSize = .zero
        text.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude,
                              height: CGFloat.greatestFiniteMagnitude)
        text.textContainer?.widthTracksTextView = false
        text.textContainer?.heightTracksTextView = false
        text.textContainer?.containerSize = text.maxSize
        scroll.documentView = text
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        update(scroll, coordinator: context.coordinator, scheme: colorScheme)
    }

    func update(_ scroll: NSScrollView, coordinator state: Coordinator, scheme: ColorScheme) {
        guard let text = scroll.documentView as? NSTextView, let storage = text.textStorage else { return }
        let rebuild = state.firstID != entries.first?.id || state.scheme != scheme
        let oldPosition = scroll.contentView.bounds.origin
        let newEntries = rebuild ? entries : entries.filter { $0.id > (state.lastID ?? -1) }
        let startedFollowing = autoScroll && !state.autoScroll
        state.autoScroll = autoScroll
        guard rebuild || !newEntries.isEmpty || startedFollowing else { return }
        storage.beginEditing()
        if rebuild { storage.setAttributedString(NSAttributedString(string: "")) }
        for entry in newEntries {
            storage.append(NSAttributedString(string: entry.text + "\n", attributes: [
                .foregroundColor: entry.level.color,
                .font: NSFont.monospacedSystemFont(ofSize: NSFont.smallSystemFontSize, weight: .regular)
            ]))
        }
        storage.endEditing()
        text.sizeToFit()
        state.firstID = entries.first?.id
        state.lastID = entries.last?.id
        state.scheme = scheme
        if autoScroll {
            // Follow vertically without resetting the user's horizontal position.
            text.scrollToEndOfDocument(nil)
            let bottom = scroll.contentView.bounds.origin.y
            scroll.contentView.scroll(to: NSPoint(x: oldPosition.x, y: bottom))
        } else {
            scroll.contentView.scroll(to: oldPosition)
        }
        scroll.reflectScrolledClipView(scroll.contentView)
    }
}
