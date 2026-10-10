import XCTest
@testable import T4MCore

/// Records calls instead of talking to Telegram.
actor FakeTelegram: TelegramActions {
    var calls: [String] = []
    var nextMessageId: Int64 = 1_048_576
    var failOn: String?
    var poll = PollResults(question: "Surf Saturday?", totalVoterCount: 1, isClosed: false, isAnonymous: false,
                           canGetVoters: true, options: [PollOptionResult(text: "Dawn patrol", voterCount: 1, votePercentage: 100, voters: nil)])

    func setFailOn(_ action: String) { failOn = action }

    private func record(_ call: String, _ action: String) throws {
        calls.append(call)
        if failOn == action { throw TDError(code: 400, message: "CHAT_ADMIN_REQUIRED") }
    }

    private func newMessage(_ chatId: Int64) -> SentMessage {
        defer { nextMessageId += 1_048_576 }
        return SentMessage(chatId: chatId, messageId: nextMessageId)
    }

    func sendPoll(chatId: Int64, question: String, options: [String], isAnonymous: Bool,
                  allowsMultipleAnswers: Bool, allowsRevoting: Bool, silent: Bool, replyTo: Int64?) async throws -> SentMessage {
        try record("poll \(chatId) \(question) \(options.joined(separator: "|")) anon=\(isAnonymous)", "send_poll")
        return newMessage(chatId)
    }

    func sendText(chatId: Int64, text: String, silent: Bool, replyTo: Int64?) async throws -> SentMessage {
        try record("text \(chatId) \(text) reply=\(replyTo.map { String($0) } ?? "-") silent=\(silent)", "send_message")
        return newMessage(chatId)
    }

    func pin(chatId: Int64, messageId: Int64, notify: Bool) async throws {
        try record("pin \(chatId) \(messageId) notify=\(notify)", "pin_message")
    }

    func unpin(chatId: Int64, messageId: Int64) async throws {
        try record("unpin \(chatId) \(messageId)", "unpin_message")
    }

    func stopPoll(chatId: Int64, messageId: Int64) async throws {
        try record("stop \(chatId) \(messageId)", "stop_poll")
    }

    func pollResults(chatId: Int64, messageId: Int64, includeVoters: Bool) async throws -> PollResults {
        try record("results \(chatId) \(messageId) voters=\(includeVoters)", "get_poll_results")
        return poll
    }
}

final class JobExecutorTests: XCTestCase {
    let chat: Int64 = -1001234567890

    func fixtureJob() throws -> BridgeJob {
        let url = try XCTUnwrap(Bundle.module.url(forResource: "claim-response", withExtension: "json", subdirectory: "Fixtures"))
        return try XCTUnwrap(BridgeCoding.decoder().decode(ClaimResponse.self, from: Data(contentsOf: url)).jobs.first)
    }

    func testRunsTheSurfWorkflowAndResolvesStepReferences() async throws {
        let job = try fixtureJob()
        let telegram = FakeTelegram()
        let report = await JobExecutor(telegram: telegram, allowedChatIds: [chat]).run(job)

        XCTAssertEqual(report.status, .succeeded)
        XCTAssertNil(report.error)
        let calls = await telegram.calls
        XCTAssertEqual(calls, [
            "poll \(chat) Surf Saturday? Dawn patrol|Midday|Out anon=false",
            "pin \(chat) 1048576 notify=false",
            "text \(chat) Vote above! 🏄 reply=1048576 silent=true",
            "results \(chat) 1048576 voters=true",
        ])
        XCTAssertEqual(report.steps.map(\.ok), [true, true, true, true])
        XCTAssertEqual(report.steps[0].result?.messageId, 1_048_576)
        XCTAssertEqual(report.steps[2].result?.messageId, 2_097_152)
        XCTAssertEqual(report.steps[3].result?.poll?.question, "Surf Saturday?")
    }

    func testRefusesJobsForChatsThatAreNotAllowed() async throws {
        let job = try fixtureJob()
        let telegram = FakeTelegram()
        let report = await JobExecutor(telegram: telegram, allowedChatIds: [-42]).run(job)

        XCTAssertEqual(report.status, .failed)
        XCTAssertTrue(report.error?.contains("not in the allowed groups") == true)
        XCTAssertTrue(report.steps.allSatisfy { $0.skipped == true })
        let calls = await telegram.calls
        XCTAssertEqual(calls, [], "nothing may be sent when any chat is not allowed")
    }

    func testStopsAtTheFirstFailureAndSkipsTheRest() async throws {
        let job = try fixtureJob()
        let telegram = FakeTelegram()
        await telegram.setFailOn("pin_message")
        let report = await JobExecutor(telegram: telegram, allowedChatIds: [chat]).run(job)

        XCTAssertEqual(report.status, .failed)
        XCTAssertEqual(report.steps.map(\.ok), [true, false, false, false])
        XCTAssertEqual(report.steps[1].error, "CHAT_ADMIN_REQUIRED (400)")
        XCTAssertEqual(report.steps[2].skipped, true)
        XCTAssertEqual(report.error, "Step 1 (pin_message) failed: CHAT_ADMIN_REQUIRED (400)")
        let calls = await telegram.calls
        XCTAssertEqual(calls.count, 2)
    }

    func testUnknownActionsFailClearly() async throws {
        let job = BridgeJob(id: "j", steps: [JobStep(action: "delete_everything", chatId: chat)])
        let report = await JobExecutor(telegram: FakeTelegram(), allowedChatIds: [chat]).run(job)
        XCTAssertEqual(report.status, .failed)
        XCTAssertTrue(report.steps[0].error?.contains("doesn't support") == true)
    }

    func testSummary() async throws {
        let job = try fixtureJob()
        let report = await JobExecutor(telegram: FakeTelegram(), allowedChatIds: [chat]).run(job)
        XCTAssertEqual(report.summary(for: job, chatTitles: [chat: "Surf Crew"]), "Friday surf poll in Surf Crew")
    }
}
