import Foundation

// Wire format shared with the bridge (bridge/src/validate.js). The bridge
// validates and normalizes jobs, so every step arrives with its chat_id set.

/// A message created earlier in the same job (`step`) or by a previous job (`messageId`).
public struct MessageRef: Codable, Equatable, Sendable {
    public var step: Int?
    public var messageId: Int64?

    public init(step: Int? = nil, messageId: Int64? = nil) {
        self.step = step
        self.messageId = messageId
    }
}

public struct JobStep: Codable, Equatable, Sendable {
    public var action: String
    public var chatId: Int64
    // send_poll
    public var question: String?
    public var options: [String]?
    public var isAnonymous: Bool?
    public var allowsMultipleAnswers: Bool?
    public var allowsRevoting: Bool?
    // send_poll, send_message
    public var text: String?
    public var silent: Bool?
    public var replyTo: MessageRef?
    // pin_message, unpin_message, stop_poll, get_poll_results
    public var message: MessageRef?
    public var notify: Bool?
    public var includeVoters: Bool?

    public init(action: String, chatId: Int64, question: String? = nil, options: [String]? = nil,
                isAnonymous: Bool? = nil, allowsMultipleAnswers: Bool? = nil, allowsRevoting: Bool? = nil,
                text: String? = nil, silent: Bool? = nil, replyTo: MessageRef? = nil, message: MessageRef? = nil,
                notify: Bool? = nil, includeVoters: Bool? = nil) {
        self.action = action
        self.chatId = chatId
        self.question = question
        self.options = options
        self.isAnonymous = isAnonymous
        self.allowsMultipleAnswers = allowsMultipleAnswers
        self.allowsRevoting = allowsRevoting
        self.text = text
        self.silent = silent
        self.replyTo = replyTo
        self.message = message
        self.notify = notify
        self.includeVoters = includeVoters
    }
}

public struct BridgeJob: Codable, Equatable, Sendable {
    public var id: String
    public var note: String?
    public var steps: [JobStep]

    public init(id: String, note: String? = nil, steps: [JobStep]) {
        self.id = id
        self.note = note
        self.steps = steps
    }
}

public struct ClaimResponse: Codable, Sendable {
    public var jobs: [BridgeJob]
}

// MARK: Results reported back to the bridge

public struct PollVoter: Codable, Equatable, Sendable {
    public var id: Int64
    public var name: String
    public var username: String?

    public init(id: Int64, name: String, username: String?) {
        self.id = id
        self.name = name
        self.username = username
    }
}

public struct PollOptionResult: Codable, Equatable, Sendable {
    public var text: String
    public var voterCount: Int
    public var votePercentage: Int
    public var voters: [PollVoter]?

    public init(text: String, voterCount: Int, votePercentage: Int, voters: [PollVoter]?) {
        self.text = text
        self.voterCount = voterCount
        self.votePercentage = votePercentage
        self.voters = voters
    }
}

public struct PollResults: Codable, Equatable, Sendable {
    public var question: String
    public var totalVoterCount: Int
    public var isClosed: Bool
    public var isAnonymous: Bool
    public var canGetVoters: Bool
    public var options: [PollOptionResult]

    public init(question: String, totalVoterCount: Int, isClosed: Bool, isAnonymous: Bool, canGetVoters: Bool,
                options: [PollOptionResult]) {
        self.question = question
        self.totalVoterCount = totalVoterCount
        self.isClosed = isClosed
        self.isAnonymous = isAnonymous
        self.canGetVoters = canGetVoters
        self.options = options
    }
}

public struct SentMessage: Equatable, Sendable {
    public var chatId: Int64
    public var messageId: Int64
    public var link: String?

    public init(chatId: Int64, messageId: Int64, link: String? = nil) {
        self.chatId = chatId
        self.messageId = messageId
        self.link = link
    }
}

public struct StepResult: Codable, Equatable, Sendable {
    public var messageId: Int64?
    public var chatId: Int64?
    public var link: String?
    public var poll: PollResults?

    public init(messageId: Int64? = nil, chatId: Int64? = nil, link: String? = nil, poll: PollResults? = nil) {
        self.messageId = messageId
        self.chatId = chatId
        self.link = link
        self.poll = poll
    }
}

public struct StepOutcome: Codable, Equatable, Sendable {
    public var index: Int
    public var action: String
    public var ok: Bool
    public var skipped: Bool?
    public var error: String?
    public var result: StepResult?

    public init(index: Int, action: String, ok: Bool, skipped: Bool? = nil, error: String? = nil, result: StepResult? = nil) {
        self.index = index
        self.action = action
        self.ok = ok
        self.skipped = skipped
        self.error = error
        self.result = result
    }
}

public struct JobReport: Codable, Equatable, Sendable {
    public enum Status: String, Codable, Sendable {
        case succeeded, failed
    }

    public var status: Status
    public var steps: [StepOutcome]
    public var error: String?

    public init(status: Status, steps: [StepOutcome], error: String?) {
        self.status = status
        self.steps = steps
        self.error = error
    }
}

public enum BridgeCoding {
    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return decoder
    }

    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}
