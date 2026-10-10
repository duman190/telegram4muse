import Foundation

/// What a job can do on Telegram. The app implements this with TDLib; tests use a fake.
public protocol TelegramActions: Sendable {
    func sendPoll(chatId: Int64, question: String, options: [String], isAnonymous: Bool,
                  allowsMultipleAnswers: Bool, allowsRevoting: Bool, silent: Bool, replyTo: Int64?) async throws -> SentMessage
    func sendText(chatId: Int64, text: String, silent: Bool, replyTo: Int64?) async throws -> SentMessage
    func pin(chatId: Int64, messageId: Int64, notify: Bool) async throws
    func unpin(chatId: Int64, messageId: Int64) async throws
    func stopPoll(chatId: Int64, messageId: Int64) async throws
    func pollResults(chatId: Int64, messageId: Int64, includeVoters: Bool) async throws -> PollResults
}

public struct JobError: Error, LocalizedError, Equatable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { message }
}

/// Runs a job's steps in order against Telegram. Stops at the first failure
/// and marks the remaining steps as skipped.
public struct JobExecutor: Sendable {
    public let telegram: TelegramActions
    /// Chats the user allowed on this phone. This is the real gate: the bridge
    /// checks too, but the phone holds the Telegram session, so it decides.
    public let allowedChatIds: Set<Int64>

    public init(telegram: TelegramActions, allowedChatIds: Set<Int64>) {
        self.telegram = telegram
        self.allowedChatIds = allowedChatIds
    }

    public func run(_ job: BridgeJob) async -> JobReport {
        let denied = Set(job.steps.map(\.chatId)).subtracting(allowedChatIds)
        if !denied.isEmpty {
            let list = denied.sorted().map { String($0) }.joined(separator: ", ")
            return JobReport(
                status: .failed,
                steps: job.steps.enumerated().map { StepOutcome(index: $0.offset, action: $0.element.action, ok: false, skipped: true) },
                error: "Refused on the phone: chat \(list) is not in the allowed groups. Nothing was sent."
            )
        }

        var outcomes: [StepOutcome] = []
        var created: [Int: Int64] = [:]
        var failure: String?
        for (index, step) in job.steps.enumerated() {
            if failure != nil {
                outcomes.append(StepOutcome(index: index, action: step.action, ok: false, skipped: true))
                continue
            }
            do {
                let result = try await perform(step, created: created)
                if let id = result.messageId { created[index] = id }
                outcomes.append(StepOutcome(index: index, action: step.action, ok: true, result: result))
            } catch {
                let message = error.localizedDescription
                failure = "Step \(index) (\(step.action)) failed: \(message)"
                outcomes.append(StepOutcome(index: index, action: step.action, ok: false, error: message))
            }
        }
        return JobReport(status: failure == nil ? .succeeded : .failed, steps: outcomes, error: failure)
    }

    private func resolve(_ ref: MessageRef?, created: [Int: Int64], field: String) throws -> Int64 {
        guard let ref else { throw JobError("\(field) is missing") }
        if let id = ref.messageId { return id }
        if let step = ref.step {
            guard let id = created[step] else { throw JobError("\(field) refers to step \(step), which did not create a message") }
            return id
        }
        throw JobError("\(field) needs a step or message_id")
    }

    private func perform(_ step: JobStep, created: [Int: Int64]) async throws -> StepResult {
        let chat = step.chatId
        switch step.action {
        case "send_poll":
            guard let question = step.question, let options = step.options else {
                throw JobError("send_poll needs question and options")
            }
            let replyTo = try step.replyTo.map { try resolve($0, created: created, field: "reply_to") }
            let sent = try await telegram.sendPoll(
                chatId: chat, question: question, options: options,
                isAnonymous: step.isAnonymous ?? false,
                allowsMultipleAnswers: step.allowsMultipleAnswers ?? false,
                allowsRevoting: step.allowsRevoting ?? false,
                silent: step.silent ?? false,
                replyTo: replyTo
            )
            return StepResult(messageId: sent.messageId, chatId: sent.chatId, link: sent.link)

        case "send_message":
            guard let text = step.text else { throw JobError("send_message needs text") }
            let replyTo = try step.replyTo.map { try resolve($0, created: created, field: "reply_to") }
            let sent = try await telegram.sendText(chatId: chat, text: text, silent: step.silent ?? false, replyTo: replyTo)
            return StepResult(messageId: sent.messageId, chatId: sent.chatId, link: sent.link)

        case "pin_message":
            let id = try resolve(step.message, created: created, field: "message")
            try await telegram.pin(chatId: chat, messageId: id, notify: step.notify ?? false)
            return StepResult()

        case "unpin_message":
            let id = try resolve(step.message, created: created, field: "message")
            try await telegram.unpin(chatId: chat, messageId: id)
            return StepResult()

        case "stop_poll":
            let id = try resolve(step.message, created: created, field: "message")
            try await telegram.stopPoll(chatId: chat, messageId: id)
            return StepResult()

        case "get_poll_results":
            let id = try resolve(step.message, created: created, field: "message")
            let poll = try await telegram.pollResults(chatId: chat, messageId: id, includeVoters: step.includeVoters ?? true)
            return StepResult(poll: poll)

        default:
            throw JobError("This version of the app doesn't support \"\(step.action)\". Update Telegram4Muse.")
        }
    }
}

public extension JobReport {
    /// One line for the activity log and the local notification.
    func summary(for job: BridgeJob, chatTitles: [Int64: String]) -> String {
        let chats = Set(job.steps.map(\.chatId)).map { chatTitles[$0] ?? String($0) }.sorted().joined(separator: ", ")
        let what = job.note ?? job.steps.map(\.action).joined(separator: ", ")
        switch status {
        case .succeeded: return "\(what) in \(chats)"
        case .failed: return "\(what) in \(chats) failed: \(error ?? "unknown error")"
        }
    }
}
