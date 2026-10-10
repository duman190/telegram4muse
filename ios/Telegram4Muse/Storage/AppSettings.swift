import Foundation
import T4MCore

/// Settings. Secrets (api_hash, bridge device token) go to the Keychain;
/// everything else to UserDefaults.
enum AppSettings {
    private static var defaults: UserDefaults { .standard }

    static var apiId: Int? {
        get { defaults.object(forKey: "telegram.apiId") as? Int }
        set { defaults.set(newValue, forKey: "telegram.apiId") }
    }

    static var apiHash: String? {
        get { Keychain.string("telegram.apiHash") }
        set { Keychain.setString(newValue, "telegram.apiHash") }
    }

    static var hasTelegramCredentials: Bool { apiId != nil && !(apiHash ?? "").isEmpty }

    static var telegramConfig: TelegramService.Config? {
        guard let apiId, let apiHash, !apiHash.isEmpty else { return nil }
        return TelegramService.Config(apiId: apiId, apiHash: apiHash, databaseKey: Keychain.databaseKey())
    }

    static var bridgeURL: URL? {
        get { defaults.string(forKey: "bridge.url").flatMap { URL(string: $0) } }
        set { defaults.set(newValue?.absoluteString, forKey: "bridge.url") }
    }

    static var deviceToken: String? {
        get { Keychain.string("bridge.deviceToken") }
        set { Keychain.setString(newValue, "bridge.deviceToken") }
    }

    static var bridgeClient: BridgeClient? {
        guard let url = bridgeURL, let token = deviceToken, !token.isEmpty else { return nil }
        return BridgeClient(baseURL: url, deviceToken: token)
    }

    /// The APNs device token, hex encoded.
    static var apnsToken: String? {
        get { defaults.string(forKey: "push.token") }
        set { defaults.set(newValue, forKey: "push.token") }
    }

    static var pushError: String? {
        get { defaults.string(forKey: "push.error") }
        set { defaults.set(newValue, forKey: "push.error") }
    }

    static var notifyOnJobs: Bool {
        get { defaults.object(forKey: "notifyOnJobs") as? Bool ?? true }
        set { defaults.set(newValue, forKey: "notifyOnJobs") }
    }

    /// The groups Muse may use. Enforced on this phone before any job runs.
    static var allowedChats: [TelegramChat] {
        get {
            guard let data = defaults.data(forKey: "allowedChats") else { return [] }
            return (try? JSONDecoder().decode([TelegramChat].self, from: data)) ?? []
        }
        set { defaults.set(try? JSONEncoder().encode(newValue), forKey: "allowedChats") }
    }
}

enum AppInfo {
    static var version: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
    }

    /// Builds installed from Xcode get sandbox push tokens; TestFlight and App
    /// Store builds get production ones. (The bridge also retries the other
    /// environment if Apple rejects the token.)
    static var usesSandboxPush: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }
}

/// Tells the bridge how to reach this phone and which groups are allowed.
enum BridgeSync {
    static func registerAndPublish() async throws {
        guard let bridge = AppSettings.bridgeClient else { return }
        try await bridge.register(apnsToken: AppSettings.apnsToken, sandbox: AppInfo.usesSandboxPush, appVersion: AppInfo.version)
        try await bridge.publishChats(AppSettings.allowedChats.map {
            PublishedChat(id: $0.id, title: $0.title, type: $0.kind.rawValue)
        })
    }
}
