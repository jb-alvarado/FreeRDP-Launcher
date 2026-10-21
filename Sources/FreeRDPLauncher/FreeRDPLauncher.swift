// SPDX-License-Identifier: Apache-2.0
import AppKit
import SwiftUI

@main
struct FreeRDPLauncher: App {
    @StateObject private var model: LauncherModel

    init() {
        signal(SIGPIPE, SIG_IGN)
        do {
            let configuration = try AppConfiguration.load()
            guard let identifier = Bundle.main.bundleIdentifier else {
                throw LauncherError.message("error.configuration")
            }
            _model = StateObject(wrappedValue: LauncherModel(configuration: configuration,
                                  passwordService: identifier + ".password"))
        } catch {
            let alert = NSAlert()
            alert.messageText = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String ?? ""
            alert.informativeText = (error as? LauncherError)?.description(using: Localizer(language: ""))
                ?? error.localizedDescription
            alert.runModal()
            exit(EXIT_FAILURE)
        }
    }

    var body: some Scene {
        Window("FreeRDP", id: "connection") {
            LauncherView(model: model)
                .environment(\.locale, model.localizer.locale)
                .frame(minWidth: 540, maxWidth: 900, minHeight: 640, maxHeight: 960)
                .task {
                    NSApplication.shared.activate(ignoringOtherApps: true)
                    await model.startup()
                }
        }
        .windowResizability(.contentSize)
        .defaultSize(width: 580, height: 820)
        .commands {
            LauncherCommands(model: model)
        }
        Window(Text(model.text("logs.title")), id: "logs") {
            LogWindow(model: model, logs: model.logs)
                .environment(\.locale, model.localizer.locale)
                .frame(minWidth: 440, minHeight: 240)
        }
        .defaultSize(width: 960, height: 480)
    }
}

struct LauncherCommands: Commands {
    @ObservedObject var model: LauncherModel
    @Environment(\.openWindow) private var openWindow
    var body: some Commands {
        CommandGroup(replacing: .newItem) {}
        CommandGroup(after: .appInfo) {
            Button(model.text("app.licenses")) {
                if let url = Bundle.main.url(forResource: "THIRD_PARTY_NOTICES", withExtension: "md") {
                    NSWorkspace.shared.open(url)
                }
            }
        }
        CommandGroup(after: .appSettings) {
            Picker(model.text("language.label"), selection: $model.language) {
                Text(model.text("language.system")).tag("")
                Text("Deutsch").tag("de")
                Text("English").tag("en")
            }.pickerStyle(.menu)
        }
        CommandGroup(after: .windowArrangement) {
            Button(model.text("logs.open")) { openWindow(id: "logs") }
                .keyboardShortcut("l", modifiers: [.command, .shift])
        }
        CommandGroup(replacing: .appTermination) {
            Button(model.text("app.quit")) {
                model.disconnect()
                NSApplication.shared.terminate(nil)
            }.keyboardShortcut("q")
        }
    }
}

struct LauncherView: View {
    @ObservedObject var model: LauncherModel
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                Image(nsImage: NSApplication.shared.applicationIconImage)
                    .resizable().frame(width: 48, height: 48)
                VStack(alignment: .leading) {
                    Text("FreeRDP").font(.title2.bold())
                    Text(model.text("app.subtitle")).foregroundStyle(.secondary)
                }
                Spacer()
            }
            Form {
                Section(model.text("connection.title")) {
                    HStack {
                        Picker(model.text("profiles.label"), selection: Binding(
                            get: { model.selectedProfileID },
                            set: { model.selectProfile($0) })) {
                            ForEach(model.profiles) { profile in
                                Text(profile.displayName.isEmpty ? model.text("profiles.untitled") : profile.displayName)
                                    .tag(profile.id)
                            }
                        }.pickerStyle(.menu)
                        Button { model.newConnection() } label: {
                            Label(model.text("profiles.new"), systemImage: "plus")
                        }
                    }
                    TextField(model.text("profiles.name"), text: $model.profileName)
                    TextField(model.text("connection.server"), text: $model.settings.server)
                    TextField(model.text("connection.username"), text: $model.settings.username)
                    TextField(model.text("connection.domain"), text: $model.settings.domain)
                    SecureField(model.text("connection.password"), text: $model.password)
                        .onSubmit { Task { await model.connect() } }
                    Toggle(model.text("connection.remember"), isOn: $model.settings.rememberPassword)
                    HStack {
                        Button(model.text("connection.load")) { Task { await model.loadPassword() } }
                        Button(model.text("connection.forget")) { Task { await model.forgetPassword() } }
                    }.font(.caption)
                }
                Section(model.text("display.title")) {
                    Picker(model.text("display.resolution"), selection: $model.settings.resolution) {
                        Text(model.text("display.default")).tag("")
                        ForEach(ConnectionSettings.resolutions, id: \.self) { resolution in
                            Text(resolution.replacingOccurrences(of: "x", with: " × ")).tag(resolution)
                        }
                        if !model.settings.resolution.isEmpty,
                           !ConnectionSettings.resolutions.contains(model.settings.resolution) {
                            Text(model.settings.resolution.replacingOccurrences(of: "x", with: " × "))
                                .tag(model.settings.resolution)
                        }
                    }.pickerStyle(.menu)
                    Picker(model.text("display.scale"), selection: $model.settings.desktopScale) {
                        Text(model.text("display.default")).tag("")
                        ForEach(ConnectionSettings.desktopScales, id: \.self) { scale in
                            Text("\(scale) %").tag(scale)
                        }
                        if !model.settings.desktopScale.isEmpty,
                           !ConnectionSettings.desktopScales.contains(model.settings.desktopScale) {
                            Text("\(model.settings.desktopScale) %").tag(model.settings.desktopScale)
                        }
                    }.pickerStyle(.menu)
                    Text(model.text("display.automatic")).font(.caption).foregroundStyle(.secondary)
                    Toggle(model.text("display.dynamic"), isOn: $model.settings.dynamicResolution)
                    Toggle(model.text("display.smart"), isOn: $model.settings.smartSizing)
                }
            }
            .formStyle(.grouped)
            .disabled(model.running || model.busy)
            Text(model.text("connection.hint")).font(.caption).foregroundStyle(.secondary)
            HStack {
                Text(model.text(model.statusKey)).font(.caption).foregroundStyle(.secondary)
                    .lineLimit(2).frame(maxWidth: .infinity, alignment: .leading)
                if model.busy { ProgressView().controlSize(.small) }
                Button(model.text("logs.open")) { openWindow(id: "logs") }
                if model.running {
                    Button(model.text("connection.disconnect")) { model.disconnect() }
                } else {
                    Button(model.text("connection.connect")) { Task { await model.connect() } }
                        .keyboardShortcut(.defaultAction).disabled(model.busy)
                }
            }
        }
        .padding(24)
        .alert("FreeRDP", isPresented: Binding(get: { model.error != nil },
                       set: { if !$0 { model.error = nil } })) {
            Button(model.text("app.ok")) { model.error = nil }
        } message: {
            Text(model.error?.description(using: model.localizer) ?? "")
        }
    }
}
