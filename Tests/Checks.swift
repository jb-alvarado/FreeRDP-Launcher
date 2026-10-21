// SPDX-License-Identifier: Apache-2.0
import AppKit
import Foundation

@main
struct Checks {
    @MainActor static func main() throws {
        var settings = ConnectionSettings()
        precondition(settings.server.isEmpty && settings.username.isEmpty)
        precondition(settings.width.isEmpty && settings.height.isEmpty && settings.desktopScale.isEmpty)
        settings.server = "test.invalid"
        settings.username = "test-user"
        let automatic = try settings.arguments(hasPassword: false)
        precondition(!automatic.contains { $0.hasPrefix("/size:") || $0.hasPrefix("/scale-desktop:") })
        settings.width = "1920"
        do { _ = try settings.arguments(hasPassword: false); fatalError("Accepted half a resolution") }
        catch LauncherError.message("error.resolution") {}
        settings.height = "1080"
        settings.desktopScale = "100"
        let arguments = try settings.arguments(hasPassword: true)
        precondition(arguments.contains("/from-stdin:force"))
        precondition(arguments.contains("/size:1920x1080"))
        precondition(!arguments.contains { $0.hasPrefix("/p:") })
        precondition(!arguments.contains { $0.hasPrefix("/d:") })
        let legacyAccount = settings.account
        settings.domain = "EXAMPLE"
        let domainArguments = try settings.arguments(hasPassword: false)
        precondition(domainArguments.contains("/d:EXAMPLE"))
        precondition(settings.account != legacyAccount)
        let domainAccount = settings.account
        settings.domain = "OTHER"
        precondition(settings.account != legacyAccount && settings.account != domainAccount)
        settings.domain = "bad\ndomain"
        do { _ = try settings.arguments(hasPassword: false); fatalError("Accepted domain control character") }
        catch LauncherError.message("error.identity") {}
        settings.domain = ""
        precondition(settings.account == legacyAccount)
        precondition(settings.resolution == "1920x1080")
        for resolution in ConnectionSettings.resolutions {
            settings.resolution = resolution
            let selected = try settings.arguments(hasPassword: false)
            precondition(selected.contains("/size:\(resolution)"))
        }
        for scale in ConnectionSettings.desktopScales {
            settings.desktopScale = scale
            let selected = try settings.arguments(hasPassword: false)
            precondition(selected.contains("/scale-desktop:\(scale)"))
        }
        settings.resolution = ""
        settings.desktopScale = ""
        let reset = try settings.arguments(hasPassword: false)
        precondition(settings.width.isEmpty && settings.height.isEmpty)
        precondition(!reset.contains { $0.hasPrefix("/size:") || $0.hasPrefix("/scale-desktop:") })
        settings.resolution = "1920x1080"
        settings.desktopScale = "100"
        let encoded = try JSONEncoder().encode(settings)
        precondition(!String(decoding: encoded, as: UTF8.self).contains("secret-value"))

        let suite = "FreeRDP-Launcher.Checks.\(UUID().uuidString)"
        let preferences = UserDefaults(suiteName: suite)!
        defer { preferences.removePersistentDomain(forName: suite) }
        let configuration = AppConfiguration(settingsKey: "new", legacySettingsKey: "old",
              logMaxBytes: 64, logMaxEntries: 4, logMaxLineBytes: 32)
        var legacyObject = try JSONSerialization.jsonObject(with: encoded) as! [String: Any]
        legacyObject.removeValue(forKey: "domain")
        preferences.set(try JSONSerialization.data(withJSONObject: legacyObject), forKey: "old")
        let model = LauncherModel(configuration: configuration, preferences: preferences,
                                  passwordService: suite)
        precondition(model.settings == settings)
        precondition(model.language.isEmpty) // Follow system language on first launch.
        for code: Int32 in [0, 1, 2, 11, 145] {
            model.finished(code: code, reason: .exit)
            precondition(model.error == nil && model.statusKey == "status.ended")
            precondition(model.logs.entries.last?.level == .info)
        }
        model.finished(code: 134, reason: .exit)
        guard case .exit(134)? = model.error else { fatalError("Authentication failure was hidden") }
        model.error = nil
        model.finished(code: 145, reason: .uncaughtSignal)
        guard case .exit(145)? = model.error else { fatalError("Signal termination was hidden") }
        model.error = nil
        model.logs.clear()
        let migrated = try JSONDecoder().decode(ConnectionSettings.self, from: preferences.data(forKey: "new")!)
        precondition(migrated == settings)
        model.password = "secret-value"
        model.settings.username = "someone-else"
        precondition(model.password.isEmpty && !model.settings.rememberPassword)
        model.language = "de"
        precondition(preferences.string(forKey: "language") == "de")
        model.language = ""
        precondition(preferences.string(forKey: "language") == "")

        let firstID = model.selectedProfileID
        precondition(model.profiles.count == 1 && model.profiles[0].displayName == settings.server)
        model.profileName = "Office"
        model.settings.domain = "EXAMPLE"
        model.settings.rememberPassword = true
        let firstSettings = model.settings
        model.password = "secret-value"
        model.newConnection()
        let secondID = model.selectedProfileID
        precondition(secondID != firstID && model.profiles.count == 2)
        precondition(model.settings == ConnectionSettings() && model.password.isEmpty)
        model.settings.server = "second.invalid"
        model.settings.username = "second-user"
        model.settings.domain = "SECOND"
        model.settings.resolution = "2560x1440"
        model.profileName = "   "
        precondition(model.profiles.last?.displayName == "second.invalid")
        let secondSettings = model.settings
        model.password = "secret-value"
        model.selectProfile(firstID)
        precondition(model.settings == firstSettings && model.profileName == "Office")
        precondition(model.settings.rememberPassword && model.password.isEmpty)
        model.selectProfile(secondID)
        precondition(model.settings == secondSettings && model.password.isEmpty)
        let savedProfiles = preferences.data(forKey: "new.profiles")!
        precondition(!String(decoding: savedProfiles, as: UTF8.self).contains("secret-value"))
        let restored = LauncherModel(configuration: configuration, preferences: preferences,
                                     passwordService: suite)
        precondition(restored.selectedProfileID == secondID && restored.settings == secondSettings)
        precondition(restored.profiles.count == 2 && restored.password.isEmpty)
        restored.selectProfile(firstID)
        precondition(restored.settings == firstSettings && restored.profileName == "Office")
        restored.password = "secret-value"
        restored.settings.domain = "CHANGED"
        precondition(restored.password.isEmpty && !restored.settings.rememberPassword)
        restored.selectProfile(UUID()) // Ignore nonexistent selections without losing edits.
        precondition(restored.selectedProfileID == firstID && restored.settings.domain == "CHANGED")

        // Exercise the actual translations in both directions, including regional
        // language identifiers and literal percent signs without format arguments.
        let resourceURL = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("Resources")
        let temporaryResources = FileManager.default.temporaryDirectory
            .appendingPathComponent("FreeRDP-Localization-\(UUID().uuidString)")
        try FileManager.default.copyItem(at: resourceURL, to: temporaryResources)
        defer { try? FileManager.default.removeItem(at: temporaryResources) }
        for code in ["de", "en"] {
            let strings = temporaryResources.appendingPathComponent("\(code).lproj/Localizable.strings")
            let content = try String(contentsOf: strings, encoding: .utf8)
            try (content + "\n\"test.percent\" = \"100%\";\n")
                .write(to: strings, atomically: true, encoding: .utf8)
        }
        let resources = Bundle(url: temporaryResources)!
        for code in ["de", "en", "de-DE", "en-GB", "de"] {
            let localizer = Localizer(language: code, resources: resources)
            let german = code.hasPrefix("de")
            precondition(localizer.text("connection.username") == (german ? "Benutzername" : "Username"))
            precondition(localizer.text("display.scale") == (german ? "Desktop-Skalierung" : "Desktop scale"))
            precondition(localizer.text("test.percent") == "100%")
            precondition(localizer.text("logs.count", 42) == (german ? "42 Zeilen" : "42 lines"))
            precondition(localizer.text("error.keychain", "100%").contains("100%"))
        }

        // Passwords and multi-byte UTF-8 characters split across reads must be intact
        // before redaction. Terminal escapes are removed only after redaction.
        var decoder = LogDecoder(secret: "päss-secret", maxLineBytes: 128, truncatedMessage: "oversized")
        let raw = Data("\u{1B}[31m[ERROR] päss-secret €\u{1B}[0m\ntrailing".utf8)
        var lines: [String] = []
        for byte in raw { lines += decoder.consume(Data([byte])) }
        lines += decoder.consume(Data(), final: true)
        precondition(lines == ["[ERROR] •••• €", "trailing"])
        precondition(!lines.joined().contains("päss-secret"))
        var bounded = LogDecoder(secret: "", maxLineBytes: 4, truncatedMessage: "oversized")
        precondition(bounded.consume(Data("123456789\nok\n".utf8)) == ["[WARN] oversized", "ok"])
        let logs = MemoryLogStore(maxBytes: 10, maxEntries: 2)
        logs.append(["1234", "5678", "90"])
        precondition(logs.entries.map(\.text) == ["5678", "90"] && logs.retainedBytes == 6)
        logs.append(["12345678901", "1234567890"])
        precondition(logs.entries.map(\.text) == ["1234567890"] && logs.retainedBytes == 10)
        logs.clear()
        precondition(logs.entries.isEmpty && logs.retainedBytes == 0)
        precondition(LogLevel(line: "[WARN] warning") == .warning)
        precondition(LogLevel(line: "[ERROR] failure") == .error)
        print("PASS: optional settings, migration, password isolation, UTF-8/ANSI/redaction, log bounds, severity, localization, domain and profiles")
    }
}
