import Foundation
import T4MCore
import UserNotifications

/// Claims due jobs from the bridge, runs them with TDLib and reports back.
/// Triggered by a silent push, a background refresh, opening the app, or
/// tapping the bridge's "job waiting" notification. Only one run at a time.
actor JobRunner {
    static let shared = JobRunner()

    private var current: Task<Int, Never>?

    /// Returns the number of jobs run.
    @discardableResult
    func runPending(reason: String) async -> Int {
        if let current { return await current.value }
        let task = Task { await drain(reason: reason) }
        current = task
        let count = await task.value
        current = nil
        return count
    }

    private func drain(reason: String) async -> Int {
        guard let bridge = AppSettings.bridgeClient, let config = AppSettings.telegramConfig else { return 0 }
        let telegram = TelegramService.shared
        telegram.start(config)
        do {
            // Don't take jobs unless Telegram is signed in; they stay queued on
            // the bridge (and Muse can see that) until it is.
            try await telegram.waitUntilReady(timeout: 15)
        } catch {
            return 0
        }

        let jobs: [BridgeJob]
        do {
            jobs = try await bridge.claim()
        } catch {
            return 0
        }

        let allowed = AppSettings.allowedChats
        let executor = JobExecutor(telegram: telegram, allowedChatIds: Set(allowed.map(\.id)))
        let titles = Dictionary(allowed.map { ($0.id, $0.title) }, uniquingKeysWith: { first, _ in first })
        for job in jobs {
            let report = await executor.run(job)
            let summary = report.summary(for: job, chatTitles: titles)
            ActivityLog.add(ActivityEntry(jobId: job.id, succeeded: report.status == .succeeded, summary: summary))
            await deliver(report, for: job.id, to: bridge)
            if AppSettings.notifyOnJobs {
                await Notifier.post(title: report.status == .succeeded ? "Muse posted for you" : "A Muse job failed", body: summary)
            }
        }
        return jobs.count
    }

    private func deliver(_ report: JobReport, for jobId: String, to bridge: BridgeClient) async {
        for attempt in 1...3 {
            do {
                try await bridge.report(jobId: jobId, report)
                return
            } catch {
                try? await Task.sleep(nanoseconds: UInt64(attempt) * 1_000_000_000)
            }
        }
    }
}

enum Notifier {
    static func requestPermission() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound])
    }

    static func post(title: String, body: String) async {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(request)
    }
}
