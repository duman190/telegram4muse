import Foundation
import Observation
import T4MCore

/// State behind the app's few screens.
@MainActor
@Observable
final class AppModel {
    let lock = AppLock()

    var hasTelegramCredentials = AppSettings.hasTelegramCredentials
    private(set) var authState: TelegramAuthState = .notConfigured
    private(set) var accountName: String?
    private(set) var allowedChats: [TelegramChat] = AppSettings.allowedChats
    private(set) var availableGroups: [TelegramChat] = []
    private(set) var isLoadingGroups = false
    private(set) var activity: [ActivityEntry] = ActivityLog.all()
    private(set) var bridgeMessage: String?
    private(set) var bridgeOK = false
    private(set) var notifyOnJobs = AppSettings.notifyOnJobs
    var errorMessage: String?
    var isBusy = false

    var bridgeHost: String? { AppSettings.bridgeURL?.host }

    @ObservationIgnored private var activityObserver: NSObjectProtocol?

    init() {
        TelegramService.shared.observeState { [weak self] state in
            Task { @MainActor [weak self] in self?.telegramStateChanged(state) }
        }
        activityObserver = NotificationCenter.default.addObserver(forName: .activityChanged, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in self?.activity = ActivityLog.all() }
        }
        startTelegram()
    }

    // MARK: Lifecycle

    func didBecomeActive() {
        Task {
            await syncBridge()
            await JobRunner.shared.runPending(reason: "app opened")
        }
    }

    func didEnterBackground() {
        lock.lock()
        BackgroundRefresh.schedule()
    }

    // MARK: Telegram

    private func startTelegram() {
        if let config = AppSettings.telegramConfig {
            TelegramService.shared.start(config)
        }
    }

    private func telegramStateChanged(_ state: TelegramAuthState) {
        let wasReady = authState == .ready
        authState = state
        if state == .ready, !wasReady {
            Task { await didSignIn() }
        } else if state != .ready {
            accountName = nil
        }
    }

    private func didSignIn() async {
        errorMessage = nil
        accountName = try? await TelegramService.shared.accountName()
        await Notifier.requestPermission()
        await syncBridge()
        await JobRunner.shared.runPending(reason: "signed in")
    }

    @discardableResult
    func saveTelegramCredentials(apiId: String, apiHash: String) -> Bool {
        guard let id = Int(apiId.trimmingCharacters(in: .whitespaces)), id > 0 else {
            errorMessage = "api_id is a number, like 1234567."
            return false
        }
        let hash = apiHash.trimmingCharacters(in: .whitespacesAndNewlines)
        guard hash.count == 32, hash.allSatisfy(\.isHexDigit) else {
            errorMessage = "api_hash is 32 letters and digits, like 0123456789abcdef0123456789abcdef."
            return false
        }
        AppSettings.apiId = id
        AppSettings.apiHash = hash
        errorMessage = nil
        hasTelegramCredentials = true
        startTelegram()
        return true
    }

    func submitSignIn(_ input: String) async {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        isBusy = true
        defer { isBusy = false }
        do {
            try await TelegramService.shared.submit(value)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func signOut() async {
        isBusy = true
        defer { isBusy = false }
        do {
            try await TelegramService.shared.logOut()
        } catch {
            errorMessage = error.localizedDescription
        }
        // A new sign-in starts with nothing allowed.
        allowedChats = []
        AppSettings.allowedChats = []
        availableGroups = []
        await syncBridge()
    }

    // MARK: Allowed groups

    func loadGroups() async {
        isLoadingGroups = true
        defer { isLoadingGroups = false }
        do {
            let groups = try await TelegramService.shared.groups()
            availableGroups = groups.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending }
            // Keep allowed group titles current (groups get renamed).
            let fresh = Dictionary(groups.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
            let updated = allowedChats.map { fresh[$0.id] ?? $0 }
            if updated != allowedChats { saveAllowed(updated) }
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func isAllowed(_ chat: TelegramChat) -> Bool {
        allowedChats.contains { $0.id == chat.id }
    }

    func setAllowed(_ chat: TelegramChat, _ allowed: Bool) {
        var chats = allowedChats.filter { $0.id != chat.id }
        if allowed { chats.append(chat) }
        saveAllowed(chats.sorted { $0.title.localizedCaseInsensitiveCompare($1.title) == .orderedAscending })
    }

    private func saveAllowed(_ chats: [TelegramChat]) {
        allowedChats = chats
        AppSettings.allowedChats = chats
        Task { await syncBridge() }
    }

    // MARK: Bridge

    func saveBridge(url: String, token: String) async -> Bool {
        let trimmed = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parsed = URL(string: trimmed), parsed.scheme == "https", parsed.host != nil else {
            errorMessage = "Use your bridge's https:// address."
            return false
        }
        let newToken = token.trimmingCharacters(in: .whitespacesAndNewlines)
        if newToken.isEmpty && AppSettings.deviceToken == nil {
            errorMessage = "Paste the DEVICE_TOKEN you set on the bridge."
            return false
        }
        AppSettings.bridgeURL = parsed
        if !newToken.isEmpty { AppSettings.deviceToken = newToken }
        errorMessage = nil
        await syncBridge()
        return bridgeOK
    }

    func syncBridge() async {
        guard AppSettings.bridgeClient != nil else {
            bridgeOK = false
            bridgeMessage = "Not connected. Add your bridge so Muse can reach this phone."
            return
        }
        do {
            try await BridgeSync.registerAndPublish()
            bridgeOK = true
            if AppSettings.apnsToken == nil {
                bridgeMessage = "Connected, but push isn't set up yet\(AppSettings.pushError.map { ": \($0)" } ?? ""). Jobs run when you open the app."
            } else {
                bridgeMessage = "Connected. Muse sees \(allowedChats.count) allowed group\(allowedChats.count == 1 ? "" : "s")."
            }
        } catch {
            bridgeOK = false
            bridgeMessage = error.localizedDescription
        }
    }

    func runPendingNow() async {
        let count = await JobRunner.shared.runPending(reason: "manual")
        bridgeMessage = count == 0 ? "No jobs waiting." : "Ran \(count) job\(count == 1 ? "" : "s")."
    }

    func setNotifyOnJobs(_ value: Bool) {
        notifyOnJobs = value
        AppSettings.notifyOnJobs = value
    }

    func refresh() async {
        if authState == .ready, accountName == nil {
            accountName = try? await TelegramService.shared.accountName()
        }
        await syncBridge()
        activity = ActivityLog.all()
    }
}
