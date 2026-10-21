// SPDX-License-Identifier: Apache-2.0
import AppKit
import Foundation

@MainActor
final class LauncherModel: ObservableObject {
    @Published var settings: ConnectionSettings {
        didSet {
            guard !switchingProfile else { return }
            if oldValue.account != settings.account {
                password = ""
                settings.rememberPassword = false
                statusKey = "status.changed"
            }
            persist()
        }
    }
    @Published var profileName: String {
        didSet { persist() }
    }
    @Published private(set) var profiles: [ConnectionProfile]
    @Published private(set) var selectedProfileID: UUID
    private var switchingProfile = false
    @Published var language: String {
        didSet { preferences.set(language, forKey: "language") }
    }
    @Published var password = ""
    @Published var statusKey = "status.ready"
    @Published var error: LauncherError?
    @Published private(set) var running = false
    @Published private(set) var busy = false
    let logs: MemoryLogStore
    private var process: Process?
    private var stopping = false
    private var started = false
    private let preferences: UserDefaults
    private let configuration: AppConfiguration
    private let passwordStore: PasswordStore
    var localizer: Localizer { Localizer(language: language) }
    func text(_ key: String) -> String { localizer.text(key) }

    init(configuration: AppConfiguration, preferences: UserDefaults = .standard,
         passwordService: String) {
        self.configuration = configuration
        self.preferences = preferences
        passwordStore = PasswordStore(service: passwordService)
        logs = MemoryLogStore(maxBytes: configuration.logMaxBytes,
                              maxEntries: configuration.logMaxEntries)
        language = preferences.string(forKey: "language") ?? ""
        let data = preferences.data(forKey: configuration.settingsKey)
            ?? preferences.data(forKey: configuration.legacySettingsKey)
        let previous = data.flatMap { try? JSONDecoder().decode(ConnectionSettings.self, from: $0) }
            ?? ConnectionSettings()
        let saved = preferences.data(forKey: configuration.settingsKey + ".profiles")
            .flatMap { try? JSONDecoder().decode(SavedConnections.self, from: $0) }
        let entries: [ConnectionProfile]
        if let saved, !saved.profiles.isEmpty,
           Set(saved.profiles.map(\.id)).count == saved.profiles.count {
            entries = saved.profiles
        } else {
            entries = [ConnectionProfile(settings: previous)]
        }
        let selected = entries.first { $0.id == saved?.selectedID } ?? entries[0]
        profiles = entries
        selectedProfileID = selected.id
        profileName = selected.name
        settings = selected.settings
        persist()
    }

    func startup() async {
        guard !started else { return }
        started = true
        if settings.rememberPassword { await loadPassword() }
    }

    func newConnection() {
        guard !running, !busy else { return }
        let profile = ConnectionProfile()
        profiles.append(profile)
        selectProfile(profile.id)
    }

    func selectProfile(_ id: UUID) {
        guard !running, !busy, id != selectedProfileID,
              let profile = profiles.first(where: { $0.id == id }) else { return }
        switchingProfile = true
        selectedProfileID = profile.id
        profileName = profile.name
        settings = profile.settings
        password = ""
        error = nil
        statusKey = "status.ready"
        switchingProfile = false
        persist()
    }

    func persist() {
        guard !switchingProfile,
              let index = profiles.firstIndex(where: { $0.id == selectedProfileID }) else { return }
        profiles[index].name = profileName
        profiles[index].settings = settings
        let saved = SavedConnections(profiles: profiles, selectedID: selectedProfileID)
        if let data = try? JSONEncoder().encode(saved) {
            preferences.set(data, forKey: configuration.settingsKey + ".profiles")
        }
        if let data = try? JSONEncoder().encode(settings) {
            preferences.set(data, forKey: configuration.settingsKey)
        }
    }

    private func report(_ failure: Error) {
        let failure = failure as? LauncherError ?? .system(failure.localizedDescription)
        error = failure
        logs.append(["[ERROR] " + failure.description(using: localizer)])
    }

    func loadPassword() async {
        guard !busy, !running else { return }
        let account = settings.account
        busy = true
        defer { busy = false }
        do {
            let store = passwordStore
            let saved = try await Task.detached { try store.load(account: account) }.value
            guard settings.account == account else { return }
            if let saved {
                password = saved
                statusKey = "status.passwordLoaded"
            } else { statusKey = "status.noPassword" }
        } catch { report(error) }
    }

    func forgetPassword() async {
        guard !busy, !running else { return }
        let account = settings.account
        busy = true
        defer { busy = false }
        do {
            let store = passwordStore
            try await Task.detached { try store.remove(account: account) }.value
            guard settings.account == account else { return }
            settings.rememberPassword = false
            password = ""
            statusKey = "status.passwordDeleted"
        } catch { report(error) }
    }

    func connect() async {
        guard !running, !busy else { return }
        busy = true
        defer { busy = false }
        do {
            var secret = password
            if secret.isEmpty && settings.rememberPassword {
                let store = passwordStore
                let account = settings.account
                secret = try await Task.detached { try store.load(account: account) ?? "" }.value
            }
            guard secret.utf8.count <= 511, !secret.contains("\n"),
                  !secret.contains("\r"), !secret.contains("\0") else {
                throw LauncherError.message("error.password")
            }
            let arguments = try settings.arguments(hasPassword: !secret.isEmpty)
            guard let directory = Bundle.main.executableURL?.deletingLastPathComponent() else {
                throw LauncherError.message("error.directory")
            }
            let executable = directory.appendingPathComponent("sdl-freerdp")
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                throw LauncherError.message("error.client")
            }
            if settings.rememberPassword && !secret.isEmpty {
                let store = passwordStore
                let account = settings.account
                let value = secret
                try await Task.detached { try store.save(value, account: account) }.value
            }
            let child = Process()
            child.executableURL = executable
            child.arguments = arguments
            var environment = ProcessInfo.processInfo.environment
            // The bundled client must use the pipe, even if the shell requested file logging.
            environment["WLOG_APPENDER"] = "CONSOLE"
            child.environment = environment
            let output = Pipe()
            child.standardOutput = output
            child.standardError = output
            let input = Pipe()
            child.standardInput = input
            defer { try? input.fileHandleForWriting.close() }
            try child.run()
            process = child
            running = true
            stopping = false
            statusKey = "status.running"
            logs.append(["[INFO] " + text(statusKey)])
            let logStore = logs
            let maxLine = configuration.logMaxLineBytes
            let truncated = text("logs.truncated")
            let readError = text("logs.readError")
            let redactedSecret = secret
            // Drain output off the UI thread. The store outlives the log window.
            Task.detached { [weak self] in
                let handle = output.fileHandleForReading
                defer { try? handle.close() }
                var decoder = LogDecoder(secret: redactedSecret, maxLineBytes: maxLine,
                                         truncatedMessage: truncated)
                do {
                    while let data = try handle.read(upToCount: 16_384), !data.isEmpty {
                        await logStore.append(decoder.consume(data))
                    }
                } catch {
                    // Do not log raw I/O error contents, which may include credentials.
                    await logStore.append(["[ERROR] " + readError])
                }
                await logStore.append(decoder.consume(Data(), final: true))
                child.waitUntilExit()
                await self?.finished(code: child.terminationStatus, reason: child.terminationReason)
            }
            password = ""
            if !secret.isEmpty {
                do { try input.fileHandleForWriting.write(contentsOf: Data((secret + "\n").utf8)) }
                catch {
                    stopping = true
                    child.terminate()
                    throw LauncherError.message("error.stdin")
                }
            }
        } catch { report(error) }
    }

    func finished(code: Int32, reason: Process.TerminationReason) {
        running = false
        process = nil
        statusKey = "status.ended"
        // SDL3 treats success, disconnect, logoff, user disconnect and cancelled
        // connections (including closing its window) as normal session endings.
        let normalExit = reason == .exit && [0, 1, 2, 11, 145].contains(code)
        if !normalExit && !stopping { report(LauncherError.exit(code)) }
        else { logs.append(["[INFO] " + text(statusKey)]) }
    }

    func disconnect() {
        guard let process, process.isRunning else { return }
        stopping = true
        process.terminate()
        statusKey = "status.stopping"
    }
}
