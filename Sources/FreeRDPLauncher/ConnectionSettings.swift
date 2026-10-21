// SPDX-License-Identifier: Apache-2.0
import Foundation

struct ConnectionSettings: Codable, Equatable {
    // No server, identity or preferred resolution is compiled into the app.
    var server = ""
    var username = ""
    var domain = ""
    var width = ""
    var height = ""
    var desktopScale = ""
    var dynamicResolution = false
    var smartSizing = false
    var rememberPassword = false

    init() {}

    private enum CodingKeys: String, CodingKey {
        case server, username, domain, width, height, desktopScale
        case dynamicResolution, smartSizing, rememberPassword
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        server = try values.decodeIfPresent(String.self, forKey: .server) ?? ""
        username = try values.decodeIfPresent(String.self, forKey: .username) ?? ""
        domain = try values.decodeIfPresent(String.self, forKey: .domain) ?? ""
        width = try values.decodeIfPresent(String.self, forKey: .width) ?? ""
        height = try values.decodeIfPresent(String.self, forKey: .height) ?? ""
        desktopScale = try values.decodeIfPresent(String.self, forKey: .desktopScale) ?? ""
        dynamicResolution = try values.decodeIfPresent(Bool.self, forKey: .dynamicResolution) ?? false
        smartSizing = try values.decodeIfPresent(Bool.self, forKey: .smartSizing) ?? false
        rememberPassword = try values.decodeIfPresent(Bool.self, forKey: .rememberPassword) ?? false
    }

    static let resolutions = [
        "1024x768", "1280x720", "1280x800", "1280x1024", "1366x768",
        "1440x900", "1600x900", "1680x1050", "1920x1080", "1920x1200",
        "2560x1440", "2560x1600", "3440x1440", "3840x2160"
    ]
    static let desktopScales = ["100", "125", "150", "175", "200", "225", "250",
                                "300", "350", "400", "450", "500"]

    // Keep the persisted width/height fields compatible with existing preferences.
    var resolution: String {
        get { width.isEmpty && height.isEmpty ? "" : "\(width)x\(height)" }
        set {
            let dimensions = newValue.split(separator: "x", omittingEmptySubsequences: false)
            guard dimensions.count == 2 else {
                width = ""
                height = ""
                return
            }
            width = String(dimensions[0])
            height = String(dimensions[1])
        }
    }

    var account: String {
        let base = "\(server.utf8.count):\(server)\(username)"
        // Preserve existing Keychain accounts when no domain is set.
        return domain.isEmpty ? base : "domain:\(domain.utf8.count):\(domain)\(base)"
    }

    func arguments(hasPassword: Bool) throws -> [String] {
        let controls = CharacterSet.controlCharacters
        guard !server.trimmingCharacters(in: .whitespaces).isEmpty,
              !username.trimmingCharacters(in: .whitespaces).isEmpty,
              server.rangeOfCharacter(from: controls) == nil,
              username.rangeOfCharacter(from: controls) == nil,
              domain.rangeOfCharacter(from: controls) == nil else {
            throw LauncherError.message("error.identity")
        }
        var result = ["/v:\(server)", "/u:\(username)"]
        if !domain.isEmpty { result.append("/d:\(domain)") }
        if !width.isEmpty || !height.isEmpty {
            // MS-RDPEDISP channel limits, not connection defaults.
            guard let w = Int(width), let h = Int(height),
                  (200...8192).contains(w), (200...8192).contains(h) else {
                throw LauncherError.message("error.resolution")
            }
            result.append("/size:\(w)x\(h)")
        }
        if !desktopScale.isEmpty {
            guard let scale = Int(desktopScale), (100...500).contains(scale) else {
                throw LauncherError.message("error.scale")
            }
            result.append("/scale-desktop:\(scale)")
        }
        result += [dynamicResolution ? "/dynamic-resolution" : "-dynamic-resolution",
                   smartSizing ? "/smart-sizing" : "-smart-sizing"]
        if hasPassword { result.append("/from-stdin:force") }
        return result
    }
}

struct ConnectionProfile: Codable, Equatable, Identifiable {
    var id = UUID()
    var name = ""
    var settings = ConnectionSettings()

    var displayName: String {
        let title = name.trimmingCharacters(in: .whitespacesAndNewlines)
        return title.isEmpty ? settings.server.trimmingCharacters(in: .whitespacesAndNewlines) : title
    }
}

struct SavedConnections: Codable {
    var profiles: [ConnectionProfile]
    var selectedID: UUID
}
