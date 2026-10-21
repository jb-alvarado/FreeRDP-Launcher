// SPDX-License-Identifier: Apache-2.0
import Foundation
import Security

struct PasswordStore {
    let service: String
    init(service: String) { self.service = service }

    private func query(account: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword,
         kSecAttrService as String: service,
         kSecAttrAccount as String: account]
    }

    func load(account: String) throws -> String? {
        var request = query(account: account)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess else { throw LauncherError.keychain(status) }
        guard let data = item as? Data, let value = String(data: data, encoding: .utf8) else {
            throw LauncherError.message("error.keychainData")
        }
        return value
    }

    func save(_ password: String, account: String) throws {
        let attributes = [kSecValueData as String: Data(password.utf8)]
        let status = SecItemUpdate(query(account: account) as CFDictionary, attributes as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound else { throw LauncherError.keychain(status) }
        var item = query(account: account)
        item[kSecValueData as String] = Data(password.utf8)
        item[kSecAttrLabel as String] = Bundle.main.object(forInfoDictionaryKey: "CFBundleName") as? String
        // Use the local macOS login keychain and its default per-application access control.
        // No plaintext fallback and no iCloud synchronization.
        let added = SecItemAdd(item as CFDictionary, nil)
        guard added == errSecSuccess else { throw LauncherError.keychain(added) }
    }

    func remove(account: String) throws {
        let status = SecItemDelete(query(account: account) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw LauncherError.keychain(status)
        }
    }
}
