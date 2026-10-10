import Foundation
import Security

/// Small wrapper over the iOS Keychain for this app's secrets.
///
/// Items use kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly: readable in the
/// background once the phone has been unlocked after a restart (so a push can
/// run a job while the phone is in your pocket), never synced to iCloud and
/// never restored to another device.
enum Keychain {
    private static let service = "telegram4muse"

    private static func baseQuery(_ key: String) -> [String: Any] {
        [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
    }

    static func data(_ key: String) -> Data? {
        var query = baseQuery(key)
        query[kSecReturnData as String] = true
        query[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess else { return nil }
        return result as? Data
    }

    static func setData(_ data: Data?, _ key: String) {
        SecItemDelete(baseQuery(key) as CFDictionary)
        guard let data else { return }
        var item = baseQuery(key)
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        SecItemAdd(item as CFDictionary, nil)
    }

    static func string(_ key: String) -> String? {
        data(key).flatMap { String(data: $0, encoding: .utf8) }
    }

    static func setString(_ value: String?, _ key: String) {
        setData(value.map { Data($0.utf8) }, key)
    }

    /// The key TDLib encrypts its database with. Created on first use.
    static func databaseKey() -> Data {
        if let key = data("tdlib.databaseKey"), key.count == 32 { return key }
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else {
            fatalError("No secure random bytes available")
        }
        let key = Data(bytes)
        setData(key, "tdlib.databaseKey")
        return key
    }
}
