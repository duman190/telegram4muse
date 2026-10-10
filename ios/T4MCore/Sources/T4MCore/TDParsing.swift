import Foundation

/// Where the Telegram sign-in stands, simplified for the UI.
public enum TelegramAuthState: Equatable, Sendable {
    case notConfigured
    case starting
    case waitPhoneNumber
    case waitCode(phone: String, delivery: String)
    case waitPassword(hint: String)
    case waitEmailAddress
    case waitEmailCode(pattern: String)
    /// A step this app doesn't handle; the message says what to do instead.
    case unsupported(String)
    case ready
    case loggingOut
    case closed
    case failed(String)

    /// Maps a TDLib `AuthorizationState` object. Returns nil for states the
    /// app handles internally (waiting for parameters, closing).
    public static func from(_ state: TDObject) -> TelegramAuthState? {
        switch state.tdType {
        case "authorizationStateWaitTdlibParameters", "authorizationStateClosing":
            return nil
        case "authorizationStateWaitPhoneNumber":
            return .waitPhoneNumber
        case "authorizationStateWaitCode":
            let info = state.object("code_info")
            return .waitCode(phone: info?.string("phone_number") ?? "",
                             delivery: describeCodeDelivery(info?.object("type")))
        case "authorizationStateWaitPassword":
            return .waitPassword(hint: state.string("password_hint") ?? "")
        case "authorizationStateWaitEmailAddress":
            return .waitEmailAddress
        case "authorizationStateWaitEmailCode":
            return .waitEmailCode(pattern: state.object("code_info")?.string("email_address_pattern") ?? "")
        case "authorizationStateWaitRegistration":
            return .unsupported("This number has no Telegram account yet. Sign up in the official Telegram app first, then come back.")
        case "authorizationStateWaitOtherDeviceConfirmation":
            return .unsupported("Telegram asked to confirm this login on another device. Sign out and try again with your phone number.")
        case "authorizationStateWaitPremiumPurchase":
            return .unsupported("Telegram requires a Premium purchase to sign in here. Sign in once from the official app, then try again.")
        case "authorizationStateReady":
            return .ready
        case "authorizationStateLoggingOut":
            return .loggingOut
        case "authorizationStateClosed":
            return .closed
        default:
            return nil
        }
    }

    static func describeCodeDelivery(_ type: TDObject?) -> String {
        switch type?.tdType {
        case "authenticationCodeTypeTelegramMessage": return "in your Telegram app"
        case "authenticationCodeTypeSms", "authenticationCodeTypeSmsWord", "authenticationCodeTypeSmsPhrase": return "by SMS"
        case "authenticationCodeTypeCall": return "by phone call"
        case "authenticationCodeTypeFragment": return "on fragment.com"
        default: return "by Telegram"
        }
    }

    public var isSignedIn: Bool { self == .ready }

    /// True while waiting for the person to type something.
    public var needsInput: Bool {
        switch self {
        case .waitPhoneNumber, .waitCode, .waitPassword, .waitEmailAddress, .waitEmailCode: return true
        default: return false
        }
    }
}

/// A group or channel the user could allow Muse to use.
public struct TelegramChat: Identifiable, Hashable, Codable, Sendable {
    public enum Kind: String, Codable, Sendable {
        case group, supergroup, channel
    }

    public let id: Int64
    public var title: String
    public var kind: Kind

    public init(id: Int64, title: String, kind: Kind) {
        self.id = id
        self.title = title
        self.kind = kind
    }

    /// Returns nil for private chats, bots and secret chats: only groups and
    /// channels can be allowed.
    public static func from(_ chat: TDObject) -> TelegramChat? {
        guard let id = chat.int64("id") else { return nil }
        let title = chat.string("title") ?? ""
        let type = chat.object("type")
        switch type?.tdType {
        case "chatTypeBasicGroup":
            return TelegramChat(id: id, title: title, kind: .group)
        case "chatTypeSupergroup":
            return TelegramChat(id: id, title: title, kind: type?.bool("is_channel") == true ? .channel : .supergroup)
        default:
            return nil
        }
    }
}

public extension TelegramChat.Kind {
    var label: String {
        switch self {
        case .group: return "Group"
        case .supergroup: return "Group"
        case .channel: return "Channel"
        }
    }
}

public enum TDParse {
    /// "First Last (@username)" style display name for a TDLib `user`.
    public static func voter(fromUser user: TDObject) -> PollVoter {
        let first = user.string("first_name") ?? ""
        let last = user.string("last_name") ?? ""
        let name = [first, last].filter { !$0.isEmpty }.joined(separator: " ")
        let username = (user.object("usernames")?["active_usernames"] as? [String])?.first
        return PollVoter(id: user.int64("id") ?? 0, name: name.isEmpty ? "Deleted account" : name, username: username)
    }

    public static func displayName(fromUser user: TDObject) -> String {
        let voter = voter(fromUser: user)
        return voter.username.map { "\(voter.name) (@\($0))" } ?? voter.name
    }

    /// Reads poll results from a TDLib `message` whose content is a poll.
    /// Voter lists are filled in separately.
    public static func pollResults(fromMessage message: TDObject) throws -> PollResults {
        guard let content = message.object("content"), content.tdType == "messagePoll",
              let poll = content.object("poll") else {
            throw TDError(code: 400, message: "That message is not a poll")
        }
        let options = poll.objects("options").map { option in
            PollOptionResult(
                text: option.object("text")?.string("text") ?? "",
                voterCount: option.int("voter_count") ?? 0,
                votePercentage: option.int("vote_percentage") ?? 0,
                voters: nil
            )
        }
        return PollResults(
            question: poll.object("question")?.string("text") ?? "",
            totalVoterCount: poll.int("total_voter_count") ?? 0,
            isClosed: poll.bool("is_closed"),
            isAnonymous: poll.bool("is_anonymous"),
            canGetVoters: poll.bool("can_get_voters"),
            options: options
        )
    }

    /// True while TDLib is still sending the message to Telegram's servers.
    public static func isPending(_ message: TDObject) -> Bool {
        message.object("sending_state")?.tdType == "messageSendingStatePending"
    }
}
