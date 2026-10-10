import BackgroundTasks
import T4MCore
import UIKit
import UserNotifications

final class AppDelegate: NSObject, UIApplicationDelegate {
    private let notificationDelegate = NotificationDelegate()

    func application(_ application: UIApplication,
                      didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = notificationDelegate
        BackgroundRefresh.register()
        // Start Telegram early: after a silent push the app may be launched in
        // the background with no UI at all.
        if let config = AppSettings.telegramConfig {
            TelegramService.shared.start(config)
        }
        application.registerForRemoteNotifications()
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let token = deviceToken.map { String(format: "%02x", $0) }.joined()
        let changed = token != AppSettings.apnsToken
        AppSettings.apnsToken = token
        AppSettings.pushError = nil
        if changed {
            Task { try? await BridgeSync.registerAndPublish() }
        }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        AppSettings.pushError = error.localizedDescription
    }

    /// The bridge's silent push: a job is waiting. iOS gives us about 30 seconds.
    func application(_ application: UIApplication, didReceiveRemoteNotification userInfo: [AnyHashable: Any],
                     fetchCompletionHandler completionHandler: @escaping (UIBackgroundFetchResult) -> Void) {
        let extraTime = BackgroundTime(name: "telegram4muse.push")
        Task {
            let count = await JobRunner.shared.runPending(reason: "push")
            completionHandler(count > 0 ? .newData : .noData)
            extraTime.end()
        }
    }
}

/// Shows notifications while the app is open, and runs waiting jobs when the
/// bridge's "job waiting" notification is tapped.
final class NotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list])
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        Task { await JobRunner.shared.runPending(reason: "notification tapped") }
        completionHandler()
    }
}

/// Asks iOS for a little more time to finish a job after a push.
@MainActor
final class BackgroundTime {
    private var id: UIBackgroundTaskIdentifier = .invalid

    init(name: String) {
        id = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            MainActor.assumeIsolated { self?.end() }
        }
    }

    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
    }
}

/// Opportunistic background checks for waiting jobs, in case a push was dropped.
enum BackgroundRefresh {
    static var identifier: String { (Bundle.main.bundleIdentifier ?? "telegram4muse") + ".refresh" }

    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            handle(task)
        }
    }

    static func schedule() {
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 30 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }

    private static func handle(_ task: BGTask) {
        schedule()
        let work = Task { await JobRunner.shared.runPending(reason: "background refresh") }
        task.expirationHandler = { work.cancel() }
        Task {
            _ = await work.value
            task.setTaskCompleted(success: true)
        }
    }
}
