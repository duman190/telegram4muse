import XCTest
@testable import T4MCore

/// Pins request shapes to the TDLib 1.8.68 schema and checks the parsers on
/// objects shaped like TDLib's JSON output.
final class TDLibTests: XCTestCase {
    func roundTrip(_ object: TDObject) throws -> TDObject {
        try XCTUnwrap(TDJSON.decode(try TDJSON.encode(object)))
    }

    func testPollRequest() throws {
        let content = TD.pollContent(question: "Surf Saturday?", options: ["Dawn", "Out"], isAnonymous: false,
                                     allowsMultipleAnswers: true, allowsRevoting: false)
        let request = try roundTrip(TD.sendMessage(chatId: -100123, content: content, replyTo: nil, silent: true))
        XCTAssertEqual(request.tdType, "sendMessage")
        XCTAssertEqual(request.int64("chat_id"), -100123)
        XCTAssertNil(request["reply_to"])
        XCTAssertEqual(request.object("options")?.bool("disable_notification"), true)

        let poll = try XCTUnwrap(request.object("input_message_content"))
        XCTAssertEqual(poll.tdType, "inputMessagePoll")
        XCTAssertEqual(poll.object("question")?.tdType, "formattedText")
        XCTAssertEqual(poll.object("question")?.string("text"), "Surf Saturday?")
        let options = poll.objects("options")
        XCTAssertEqual(options.map(\.tdType), ["inputPollOption", "inputPollOption"])
        XCTAssertEqual(options.map { $0.object("text")?.string("text") }, ["Dawn", "Out"])
        XCTAssertEqual(poll.bool("is_anonymous"), false)
        XCTAssertEqual(poll.bool("allows_multiple_answers"), true)
        XCTAssertEqual(poll.object("type")?.tdType, "inputPollTypeRegular")
    }

    func testReplyAndPinRequests() throws {
        let reply = try roundTrip(TD.sendMessage(chatId: 1, content: TD.textContent("hi"), replyTo: 1_048_576, silent: false))
        XCTAssertEqual(reply.object("reply_to")?.tdType, "inputMessageReplyToMessage")
        XCTAssertEqual(reply.object("reply_to")?.int64("message_id"), 1_048_576)
        XCTAssertEqual(reply.object("input_message_content")?.tdType, "inputMessageText")

        let pin = try roundTrip(TD.pinChatMessage(chatId: 1, messageId: 2, notify: false))
        XCTAssertEqual(pin.tdType, "pinChatMessage")
        XCTAssertEqual(pin.bool("disable_notification"), true)
        XCTAssertEqual(pin.bool("only_for_self"), false)
    }

    func testParametersEncodeTheDatabaseKeyAsBase64() throws {
        let key = Data(repeating: 7, count: 32)
        let params = TD.Parameters(apiId: 12345, apiHash: "hash", databaseDirectory: "/db", filesDirectory: "/files",
                                   databaseKey: key, systemLanguageCode: "en", deviceModel: "iPhone",
                                   systemVersion: "26.0", applicationVersion: "1.0")
        let request = try roundTrip(TD.setTdlibParameters(params))
        XCTAssertEqual(request.tdType, "setTdlibParameters")
        XCTAssertEqual(request.int("api_id"), 12345)
        XCTAssertEqual(request.string("database_encryption_key"), key.base64EncodedString())
        XCTAssertEqual(request.bool("use_secret_chats"), false)
    }

    func testAuthorizationStates() throws {
        let code = try XCTUnwrap(TDJSON.decode("""
        {"@type":"authorizationStateWaitCode","code_info":{"@type":"authenticationCodeInfo","phone_number":"+15551234567",
         "type":{"@type":"authenticationCodeTypeTelegramMessage","length":5},"timeout":60}}
        """))
        XCTAssertEqual(TelegramAuthState.from(code), .waitCode(phone: "+15551234567", delivery: "in your Telegram app"))

        let password = try XCTUnwrap(TDJSON.decode(#"{"@type":"authorizationStateWaitPassword","password_hint":"waves"}"#))
        XCTAssertEqual(TelegramAuthState.from(password), .waitPassword(hint: "waves"))

        XCTAssertEqual(TelegramAuthState.from(["@type": "authorizationStateReady"]), .ready)
        XCTAssertNil(TelegramAuthState.from(["@type": "authorizationStateWaitTdlibParameters"]))
        if case .unsupported = TelegramAuthState.from(["@type": "authorizationStateWaitRegistration"]) {} else {
            XCTFail("registration should be unsupported")
        }
    }

    func testChatParsingKeepsOnlyGroupsAndChannels() throws {
        let supergroup = try XCTUnwrap(TDJSON.decode("""
        {"@type":"chat","id":-1001234567890,"title":"Surf Crew","type":{"@type":"chatTypeSupergroup","supergroup_id":1234567890,"is_channel":false}}
        """))
        XCTAssertEqual(TelegramChat.from(supergroup), TelegramChat(id: -1001234567890, title: "Surf Crew", kind: .supergroup))

        let channel = try XCTUnwrap(TDJSON.decode("""
        {"@type":"chat","id":-1009,"title":"News","type":{"@type":"chatTypeSupergroup","supergroup_id":9,"is_channel":true}}
        """))
        XCTAssertEqual(TelegramChat.from(channel)?.kind, .channel)

        let basic = try XCTUnwrap(TDJSON.decode(#"{"@type":"chat","id":-42,"title":"Old group","type":{"@type":"chatTypeBasicGroup","basic_group_id":42}}"#))
        XCTAssertEqual(TelegramChat.from(basic)?.kind, .group)

        let dm = try XCTUnwrap(TDJSON.decode(#"{"@type":"chat","id":777,"title":"Mom","type":{"@type":"chatTypePrivate","user_id":777}}"#))
        XCTAssertNil(TelegramChat.from(dm))
    }

    func testPollParsing() throws {
        let message = try XCTUnwrap(TDJSON.decode("""
        {"@type":"message","id":1048576,"chat_id":-100,"content":{"@type":"messagePoll","poll":{"@type":"poll","id":"5432109876543210",
          "question":{"@type":"formattedText","text":"Surf Saturday?","entities":[]},
          "options":[
            {"@type":"pollOption","id":"0","text":{"@type":"formattedText","text":"Dawn patrol","entities":[]},"voter_count":2,"vote_percentage":67,"is_chosen":false,"is_being_chosen":false},
            {"@type":"pollOption","id":"1","text":{"@type":"formattedText","text":"Out","entities":[]},"voter_count":1,"vote_percentage":33,"is_chosen":true,"is_being_chosen":false}
          ],
          "total_voter_count":3,"can_get_voters":true,"is_anonymous":false,"allows_multiple_answers":false,"is_closed":false}}}
        """))
        let results = try TDParse.pollResults(fromMessage: message)
        XCTAssertEqual(results.question, "Surf Saturday?")
        XCTAssertEqual(results.totalVoterCount, 3)
        XCTAssertEqual(results.options.map(\.text), ["Dawn patrol", "Out"])
        XCTAssertEqual(results.options.map(\.voterCount), [2, 1])
        XCTAssertTrue(results.canGetVoters)

        let text = try XCTUnwrap(TDJSON.decode(#"{"@type":"message","id":1,"content":{"@type":"messageText"}}"#))
        XCTAssertThrowsError(try TDParse.pollResults(fromMessage: text))
    }

    func testUserNames() throws {
        let user = try XCTUnwrap(TDJSON.decode("""
        {"@type":"user","id":11,"first_name":"Kai","last_name":"Moana","usernames":{"@type":"usernames","active_usernames":["kai"],"disabled_usernames":[],"editable_username":"kai"}}
        """))
        XCTAssertEqual(TDParse.voter(fromUser: user), PollVoter(id: 11, name: "Kai Moana", username: "kai"))
        XCTAssertEqual(TDParse.displayName(fromUser: user), "Kai Moana (@kai)")
    }

    func testPendingState() {
        XCTAssertTrue(TDParse.isPending(["sending_state": ["@type": "messageSendingStatePending", "sending_id": 0] as TDObject]))
        XCTAssertFalse(TDParse.isPending(["@type": "message"]))
    }
}

final class BridgeContractTests: XCTestCase {
    func fixture(_ name: String) throws -> Data {
        let url = try XCTUnwrap(Bundle.module.url(forResource: name, withExtension: "json", subdirectory: "Fixtures"))
        return try Data(contentsOf: url)
    }

    func testDecodesTheBridgeClaimResponse() throws {
        let jobs = try BridgeCoding.decoder().decode(ClaimResponse.self, from: fixture("claim-response")).jobs
        XCTAssertEqual(jobs.count, 1)
        let steps = jobs[0].steps
        XCTAssertEqual(steps.map(\.action), ["send_poll", "pin_message", "send_message", "get_poll_results"])
        XCTAssertEqual(steps[0].options, ["Dawn patrol", "Midday", "Out"])
        XCTAssertEqual(steps[0].isAnonymous, false)
        XCTAssertEqual(steps[1].message, MessageRef(step: 0))
        XCTAssertEqual(steps[2].replyTo, MessageRef(step: 0))
        XCTAssertEqual(steps[2].silent, true)
        XCTAssertEqual(steps[3].message, MessageRef(messageId: 1_048_576))
        XCTAssertEqual(steps[3].includeVoters, true)
        XCTAssertTrue(steps.allSatisfy { $0.chatId == -1001234567890 })
    }

    func testReportEncodingMatchesWhatTheBridgeExpects() throws {
        let fromFixture = try BridgeCoding.decoder().decode(JobReport.self, from: fixture("job-report"))
        XCTAssertEqual(fromFixture.status, .succeeded)
        XCTAssertEqual(fromFixture.steps[3].result?.poll?.options[0].voters?[0], PollVoter(id: 11, name: "Kai Moana", username: "kai"))

        // What the app would send for the same run decodes to the same thing.
        let encoded = try BridgeCoding.encoder().encode(fromFixture)
        let json = try XCTUnwrap(String(data: encoded, encoding: .utf8))
        XCTAssertTrue(json.contains("\"message_id\":1048576"), json)
        XCTAssertTrue(json.contains("\"total_voter_count\":3"), json)
        XCTAssertFalse(json.contains("\"error\""), "nil fields are left out")
        XCTAssertEqual(try BridgeCoding.decoder().decode(JobReport.self, from: encoded), fromFixture)
    }
}
