import Foundation
import T4MCore

/// Everything the app does with Telegram, on top of TDLib. One instance per
/// process; safe to use from any thread.
final class TelegramService: TelegramActions, @unchecked Sendable {
    static let shared = TelegramService()

    struct Config: Equatable {
        var apiId: Int
        var apiHash: String
        var databaseKey: Data
    }

    private struct SendKey: Hashable {
        let chatId: Int64
        let messageId: Int64
    }

    private let lock = NSLock()
    private var client: TDClient?
    private var config: Config?
    private var state: TelegramAuthState = .notConfigured
    private var stateObserver: ((TelegramAuthState) -> Void)?
    private var sendWaiters: [SendKey: (Result<TDObject, Error>) -> Void] = [:]
    private var finishedSends: [SendKey: Result<TDObject, Error>] = [:]

    var authState: TelegramAuthState { lock.withLock { state } }

    /// `handler` is called right away with the current state, then on every
    /// change, on an arbitrary thread.
    func observeState(_ handler: @escaping (TelegramAuthState) -> Void) {
        let current = lock.withLock {
            stateObserver = handler
            return state
        }
        handler(current)
    }

    /// Starts TDLib if it isn't running yet. Safe to call repeatedly.
    func start(_ config: Config) {
        let client: TDClient? = lock.withLock {
            guard self.client == nil else { return nil }
            self.config = config
            let newClient = TDClient { [weak self] update in self?.handle(update) }
            self.client = newClient
            return newClient
        }
        guard let client else { return }
        TDClient.execute(TD.setLogVerbosityLevel(1))
        setState(.starting)
        // TDLib starts on its first request, then asks for its parameters
        // through updateAuthorizationState.
        Task { _ = try? await client.send(["@type": "getOption", "name": "version"]) }
    }

    func request(_ object: TDObject, timeout: TimeInterval = 30) async throws -> TDObject {
        guard let client = lock.withLock({ self.client }) else {
            throw TDError(code: 0, message: "Telegram isn't running yet")
        }
        return try await client.send(object, timeout: timeout)
    }

    // MARK: Updates

    private func handle(_ update: TDObject) {
        switch update.tdType {
        case "updateAuthorizationState":
            guard let object = update.object("authorization_state") else { return }
            switch object.tdType {
            case "authorizationStateWaitTdlibParameters":
                sendParameters()
            case "authorizationStateClosed":
                let config = lock.withLock {
                    client = nil
                    return self.config
                }
                setState(.closed)
                // TDLib closes after a sign-out; start a fresh instance so the
                // person can sign in again.
                if let config { start(config) }
            default:
                if let state = TelegramAuthState.from(object) { setState(state) }
            }
        case "updateMessageSendSucceeded":
            guard let message = update.object("message"), let chatId = message.int64("chat_id"),
                  let oldId = update.int64("old_message_id") else { return }
            completeSend(SendKey(chatId: chatId, messageId: oldId), .success(message))
        case "updateMessageSendFailed":
            guard let message = update.object("message"), let chatId = message.int64("chat_id"),
                  let oldId = update.int64("old_message_id") else { return }
            let error = update.object("error").map { TDError($0) } ?? TDError(code: 0, message: "Sending failed")
            completeSend(SendKey(chatId: chatId, messageId: oldId), .failure(error))
        default:
            break
        }
    }

    private func setState(_ new: TelegramAuthState) {
        let observer = lock.withLock {
            state = new
            return stateObserver
        }
        observer?(new)
    }

    private func sendParameters() {
        let (client, config) = lock.withLock { (self.client, self.config) }
        guard let client, let config else { return }
        let directories = Self.directories()
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let parameters = TD.Parameters(
            apiId: config.apiId,
            apiHash: config.apiHash,
            databaseDirectory: directories.database.path,
            filesDirectory: directories.files.path,
            databaseKey: config.databaseKey,
            systemLanguageCode: Locale.preferredLanguages.first ?? "en",
            deviceModel: "iPhone (Telegram4Muse)",
            systemVersion: "iOS \(os.majorVersion).\(os.minorVersion)",
            applicationVersion: AppInfo.version
        )
        Task {
            do {
                _ = try await client.send(TD.setTdlibParameters(parameters))
            } catch {
                setState(.failed("Telegram couldn't start: \(error.localizedDescription)"))
            }
        }
    }

    /// TDLib's database lives in Application Support, encrypted with a key from
    /// the Keychain. It stays readable after the first unlock so background
    /// pushes can use it, and is excluded from backups.
    private static func directories() -> (database: URL, files: URL) {
        let fm = FileManager.default
        let database = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("tdlib", isDirectory: true)
        let files = fm.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("tdlib-files", isDirectory: true)
        for var url in [database, files] {
            try? fm.createDirectory(at: url, withIntermediateDirectories: true,
                                    attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication])
            var values = URLResourceValues()
            values.isExcludedFromBackup = true
            try? url.setResourceValues(values)
        }
        return (database, files)
    }

    // MARK: Sign-in

    /// Submits whatever the current sign-in step asks for.
    func submit(_ input: String) async throws {
        switch authState {
        case .waitPhoneNumber: _ = try await request(TD.setAuthenticationPhoneNumber(input))
        case .waitCode: _ = try await request(TD.checkAuthenticationCode(input))
        case .waitPassword: _ = try await request(TD.checkAuthenticationPassword(input))
        case .waitEmailAddress: _ = try await request(TD.setAuthenticationEmailAddress(input))
        case .waitEmailCode: _ = try await request(TD.checkAuthenticationEmailCode(input))
        default: break
        }
    }

    /// Signs out and deletes the local Telegram database.
    func logOut() async throws {
        _ = try await request(TD.logOut)
    }

    func accountName() async throws -> String {
        let me = try await request(TD.getMe)
        return TDParse.displayName(fromUser: me)
    }

    /// Waits for an existing session to come up (used before running jobs).
    func waitUntilReady(timeout: TimeInterval) async throws {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            let state = authState
            switch state {
            case .ready:
                return
            case .notConfigured:
                throw JobError("Telegram isn't set up on the phone yet.")
            case .failed(let message):
                throw JobError(message)
            case .unsupported, .loggingOut:
                throw JobError("Telegram is signed out on the phone. Open Telegram4Muse and sign in.")
            default:
                if state.needsInput { throw JobError("Telegram is signed out on the phone. Open Telegram4Muse and sign in.") }
            }
            if Date() >= deadline { throw JobError("Telegram didn't start in time.") }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
    }

    // MARK: Chats

    /// Asks TDLib to load the chat lists into its local database. loadChats
    /// returns a 404 error once a list is fully loaded.
    private func loadChatLists() async {
        for list in TD.ChatList.allCases {
            for _ in 0..<20 {
                do {
                    _ = try await request(TD.loadChats(list, limit: 100))
                } catch {
                    break
                }
            }
        }
    }

    /// Groups and channels the user is in (main list and archive).
    func groups() async throws -> [TelegramChat] {
        await loadChatLists()
        var seen = Set<Int64>()
        var result: [TelegramChat] = []
        for list in TD.ChatList.allCases {
            let ids = try await request(TD.getChats(list, limit: 2000)).int64s("chat_ids")
            for id in ids where seen.insert(id).inserted {
                if let chat = try? await request(TD.getChat(id)), let group = TelegramChat.from(chat) {
                    result.append(group)
                }
            }
        }
        return result
    }

    private func ensureChatLoaded(_ chatId: Int64) async throws {
        if (try? await request(TD.getChat(chatId))) != nil { return }
        await loadChatLists()
        _ = try await request(TD.getChat(chatId))
    }

    // MARK: TelegramActions

    func sendPoll(chatId: Int64, question: String, options: [String], isAnonymous: Bool,
                  allowsMultipleAnswers: Bool, allowsRevoting: Bool, silent: Bool, replyTo: Int64?) async throws -> SentMessage {
        let content = TD.pollContent(question: question, options: options, isAnonymous: isAnonymous,
                                     allowsMultipleAnswers: allowsMultipleAnswers, allowsRevoting: allowsRevoting)
        return try await sendAndConfirm(chatId: chatId, content: content, replyTo: replyTo, silent: silent)
    }

    func sendText(chatId: Int64, text: String, silent: Bool, replyTo: Int64?) async throws -> SentMessage {
        try await sendAndConfirm(chatId: chatId, content: TD.textContent(text), replyTo: replyTo, silent: silent)
    }

    func pin(chatId: Int64, messageId: Int64, notify: Bool) async throws {
        try await ensureChatLoaded(chatId)
        _ = try await request(TD.pinChatMessage(chatId: chatId, messageId: messageId, notify: notify))
    }

    func unpin(chatId: Int64, messageId: Int64) async throws {
        try await ensureChatLoaded(chatId)
        _ = try await request(TD.unpinChatMessage(chatId: chatId, messageId: messageId))
    }

    func stopPoll(chatId: Int64, messageId: Int64) async throws {
        try await ensureChatLoaded(chatId)
        _ = try await request(TD.stopPoll(chatId: chatId, messageId: messageId))
    }

    func pollResults(chatId: Int64, messageId: Int64, includeVoters: Bool) async throws -> PollResults {
        try await ensureChatLoaded(chatId)
        let message = try await request(TD.getMessage(chatId: chatId, messageId: messageId))
        var results = try TDParse.pollResults(fromMessage: message)
        guard includeVoters, !results.isAnonymous, results.canGetVoters else { return results }
        for index in results.options.indices {
            results.options[index].voters = try await voters(chatId: chatId, messageId: messageId, option: index)
        }
        return results
    }

    private func voters(chatId: Int64, messageId: Int64, option: Int) async throws -> [PollVoter] {
        var voters: [PollVoter] = []
        while voters.count < 1000 {
            let page = try await request(TD.getPollVoters(chatId: chatId, messageId: messageId, optionId: option,
                                                          offset: voters.count, limit: 50))
            let entries = page.objects("voters")
            if entries.isEmpty { break }
            for entry in entries {
                if let sender = entry.object("voter_id") { voters.append(await describe(sender)) }
            }
            if voters.count >= page.int("total_count") ?? 0 { break }
        }
        return voters
    }

    private func describe(_ sender: TDObject) async -> PollVoter {
        if sender.tdType == "messageSenderUser", let userId = sender.int64("user_id"),
           let user = try? await request(TD.getUser(userId)) {
            return TDParse.voter(fromUser: user)
        }
        if sender.tdType == "messageSenderChat", let chatId = sender.int64("chat_id"),
           let chat = try? await request(TD.getChat(chatId)) {
            return PollVoter(id: chatId, name: chat.string("title") ?? "Chat", username: nil)
        }
        return PollVoter(id: 0, name: "Unknown", username: nil)
    }

    // MARK: Sending

    /// Sends a message and waits until Telegram's servers accept it. TDLib first
    /// returns a temporary message; the real id arrives in updateMessageSendSucceeded.
    private func sendAndConfirm(chatId: Int64, content: TDObject, replyTo: Int64?, silent: Bool) async throws -> SentMessage {
        try await ensureChatLoaded(chatId)
        var message = try await request(TD.sendMessage(chatId: chatId, content: content, replyTo: replyTo, silent: silent))
        if TDParse.isPending(message), let temporaryId = message.int64("id") {
            message = try await waitForSend(SendKey(chatId: chatId, messageId: temporaryId), timeout: 30)
        }
        guard let id = message.int64("id") else {
            throw TDError(code: 0, message: "Telegram didn't return a message id")
        }
        let link = try? await request(TD.getMessageLink(chatId: chatId, messageId: id)).string("link")
        return SentMessage(chatId: chatId, messageId: id, link: link)
    }

    private func waitForSend(_ key: SendKey, timeout: TimeInterval) async throws -> TDObject {
        try await withCheckedThrowingContinuation { continuation in
            // The update may already have arrived while the request was returning.
            let early: Result<TDObject, Error>? = lock.withLock {
                if let done = finishedSends.removeValue(forKey: key) { return done }
                sendWaiters[key] = { continuation.resume(with: $0) }
                return nil
            }
            if let early {
                continuation.resume(with: early)
                return
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self] in
                self?.completeSend(key, .failure(TDError(code: 408, message: "Telegram didn't confirm the message in time")),
                                   keepIfNobodyWaits: false)
            }
        }
    }

    private func completeSend(_ key: SendKey, _ result: Result<TDObject, Error>, keepIfNobodyWaits: Bool = true) {
        let waiter = lock.withLock {
            let waiter = sendWaiters.removeValue(forKey: key)
            if waiter == nil && keepIfNobodyWaits { finishedSends[key] = result }
            return waiter
        }
        waiter?(result)
    }
}
