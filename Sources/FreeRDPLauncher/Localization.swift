// SPDX-License-Identifier: Apache-2.0
import Foundation
import Security

struct Localizer {
    let language: String
    var resources: Bundle = .main

    private var resolvedLanguage: String {
        let preferences = language.isEmpty ? Locale.preferredLanguages : [language]
        return Bundle.preferredLocalizations(from: resources.localizations,
                                             forPreferences: preferences).first ?? "en"
    }
    var locale: Locale { Locale(identifier: resolvedLanguage) }
    var bundle: Bundle {
        guard let path = resources.path(forResource: resolvedLanguage, ofType: "lproj"),
              let localized = Bundle(path: path) else { return resources }
        return localized
    }
    func text(_ key: String, _ arguments: CVarArg...) -> String {
        let format = bundle.localizedString(forKey: key, value: key, table: nil)
        guard !arguments.isEmpty else { return format }
        return String(format: format, locale: locale, arguments: arguments)
    }
}

struct AppConfiguration: Decodable {
    let settingsKey: String
    let legacySettingsKey: String
    let logMaxBytes: Int
    let logMaxEntries: Int
    let logMaxLineBytes: Int

    static func load(bundle: Bundle = .main) throws -> AppConfiguration {
        guard let url = bundle.url(forResource: "LauncherConfig", withExtension: "json") else {
            throw LauncherError.message("error.configuration")
        }
        let configuration = try JSONDecoder().decode(Self.self, from: Data(contentsOf: url))
        guard configuration.logMaxBytes > 0, configuration.logMaxEntries > 0,
              configuration.logMaxLineBytes > 0 else {
            throw LauncherError.message("error.configuration")
        }
        return configuration
    }
}

enum LauncherError: Error {
    case message(String)
    case keychain(OSStatus)
    case system(String)
    case exit(Int32)

    func description(using localizer: Localizer) -> String {
        switch self {
        case .message(let key): return localizer.text(key)
        case .keychain(let status):
            let detail = SecCopyErrorMessageString(status, nil) as String? ?? String(status)
            return localizer.text("error.keychain", detail)
        case .system(let text): return text
        case .exit(let code): return localizer.text("error.exit", code)
        }
    }
}
