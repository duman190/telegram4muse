import Foundation

/// Builders for the TDLib requests this app sends, written against the TDLib
/// 1.8.68 schema (td_api.tl). Kept in one place so tests can pin the shapes.
public enum TD {
    public static func formattedText(_ text: String) -> TDObject {
        ["@type": "formattedText", "text": text, "entities": [Any]()]
    }

    // MARK: Setup and sign-in

    public struct Parameters {
        public var apiId: Int
        public var apiHash: String
        public var databaseDirectory: String
        public var filesDirectory: String
        public var databaseKey: Data
        public var systemLanguageCode: String
        public var deviceModel: String
        public var systemVersion: String
        public var applicationVersion: String

        public init(apiId: Int, apiHash: String, databaseDirectory: String, filesDirectory: String, databaseKey: Data,
                    systemLanguageCode: String, deviceModel: String, systemVersion: String, applicationVersion: String) {
            self.apiId = apiId
            self.apiHash = apiHash
            self.databaseDirectory = databaseDirectory
            self.filesDirectory = filesDirectory
            self.databaseKey = databaseKey
            self.systemLanguageCode = systemLanguageCode
            self.deviceModel = deviceModel
            self.systemVersion = systemVersion
            self.applicationVersion = applicationVersion
        }
    }

    public static func setTdlibParameters(_ p: Parameters) -> TDObject {
        [
            "@type": "setTdlibParameters",
            "use_test_dc": false,
            "database_directory": p.databaseDirectory,
            "files_directory": p.filesDirectory,
            // `bytes` fields are base64 in TDLib's JSON interface.
            "database_encryption_key": p.databaseKey.base64EncodedString(),
            "use_file_database": false,
            "use_chat_info_database": true,
            "use_message_database": true,
            "use_secret_chats": false,
            "api_id": p.apiId,
            "api_hash": p.apiHash,
            "system_language_code": p.systemLanguageCode,
            "device_model": p.deviceModel,
            "system_version": p.systemVersion,
            "application_version": p.applicationVersion,
        ]
    }

    public static func setLogVerbosityLevel(_ level: Int) -> TDObject {
        ["@type": "setLogVerbosityLevel", "new_verbosity_level": level]
    }

    public static func setAuthenticationPhoneNumber(_ phone: String) -> TDObject {
        ["@type": "setAuthenticationPhoneNumber", "phone_number": phone]
    }

    public static func checkAuthenticationCode(_ code: String) -> TDObject {
        ["@type": "checkAuthenticationCode", "code": code]
    }

    public static func checkAuthenticationPassword(_ password: String) -> TDObject {
        ["@type": "checkAuthenticationPassword", "password": password]
    }

    public static func setAuthenticationEmailAddress(_ email: String) -> TDObject {
        ["@type": "setAuthenticationEmailAddress", "email_address": email]
    }

    public static func checkAuthenticationEmailCode(_ code: String) -> TDObject {
        ["@type": "checkAuthenticationEmailCode", "code": ["@type": "emailAddressAuthenticationCode", "code": code]]
    }

    public static let getMe: TDObject = ["@type": "getMe"]
    public static let logOut: TDObject = ["@type": "logOut"]

    // MARK: Chats

    public enum ChatList: CaseIterable {
        case main, archive

        var object: TDObject {
            switch self {
            case .main: return ["@type": "chatListMain"]
            case .archive: return ["@type": "chatListArchive"]
            }
        }
    }

    public static func loadChats(_ list: ChatList, limit: Int) -> TDObject {
        ["@type": "loadChats", "chat_list": list.object, "limit": limit]
    }

    public static func getChats(_ list: ChatList, limit: Int) -> TDObject {
        ["@type": "getChats", "chat_list": list.object, "limit": limit]
    }

    public static func getChat(_ chatId: Int64) -> TDObject {
        ["@type": "getChat", "chat_id": chatId]
    }

    public static func getUser(_ userId: Int64) -> TDObject {
        ["@type": "getUser", "user_id": userId]
    }

    // MARK: Messages

    public static func pollContent(question: String, options: [String], isAnonymous: Bool,
                                   allowsMultipleAnswers: Bool, allowsRevoting: Bool) -> TDObject {
        [
            "@type": "inputMessagePoll",
            "question": formattedText(question),
            "options": options.map { ["@type": "inputPollOption", "text": formattedText($0)] as TDObject },
            "is_anonymous": isAnonymous,
            "allows_multiple_answers": allowsMultipleAnswers,
            "allows_revoting": allowsRevoting,
            "type": ["@type": "inputPollTypeRegular", "allow_adding_options": false] as TDObject,
        ]
    }

    public static func textContent(_ text: String) -> TDObject {
        ["@type": "inputMessageText", "text": formattedText(text)]
    }

    public static func sendMessage(chatId: Int64, content: TDObject, replyTo: Int64?, silent: Bool) -> TDObject {
        var request: TDObject = [
            "@type": "sendMessage",
            "chat_id": chatId,
            "options": ["@type": "messageSendOptions", "disable_notification": silent] as TDObject,
            "input_message_content": content,
        ]
        if let replyTo {
            request["reply_to"] = ["@type": "inputMessageReplyToMessage", "message_id": replyTo] as TDObject
        }
        return request
    }

    public static func pinChatMessage(chatId: Int64, messageId: Int64, notify: Bool) -> TDObject {
        ["@type": "pinChatMessage", "chat_id": chatId, "message_id": messageId,
         "disable_notification": !notify, "only_for_self": false]
    }

    public static func unpinChatMessage(chatId: Int64, messageId: Int64) -> TDObject {
        ["@type": "unpinChatMessage", "chat_id": chatId, "message_id": messageId]
    }

    public static func stopPoll(chatId: Int64, messageId: Int64) -> TDObject {
        ["@type": "stopPoll", "chat_id": chatId, "message_id": messageId]
    }

    public static func getMessage(chatId: Int64, messageId: Int64) -> TDObject {
        ["@type": "getMessage", "chat_id": chatId, "message_id": messageId]
    }

    public static func getMessageLink(chatId: Int64, messageId: Int64) -> TDObject {
        ["@type": "getMessageLink", "chat_id": chatId, "message_id": messageId, "media_timestamp": 0,
         "checklist_task_id": 0, "poll_option_id": "", "for_album": false, "in_message_thread": false]
    }

    public static func getPollVoters(chatId: Int64, messageId: Int64, optionId: Int, offset: Int, limit: Int) -> TDObject {
        ["@type": "getPollVoters", "chat_id": chatId, "message_id": messageId,
         "option_id": optionId, "offset": offset, "limit": limit]
    }
}
