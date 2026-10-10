import Foundation

struct ActivityEntry: Codable, Identifiable, Hashable {
    var id = UUID()
    var date = Date()
    var jobId: String?
    var succeeded: Bool
    var summary: String
}

extension Notification.Name {
    static let activityChanged = Notification.Name("telegram4muse.activityChanged")
}

/// The last 50 things Muse did through this phone, shown on the home screen.
enum ActivityLog {
    private static let key = "activityLog"
    private static let lock = NSLock()

    static func all() -> [ActivityEntry] {
        guard let data = UserDefaults.standard.data(forKey: key) else { return [] }
        return (try? JSONDecoder().decode([ActivityEntry].self, from: data)) ?? []
    }

    static func add(_ entry: ActivityEntry) {
        lock.withLock {
            let entries = Array(([entry] + all()).prefix(50))
            UserDefaults.standard.set(try? JSONEncoder().encode(entries), forKey: key)
        }
        NotificationCenter.default.post(name: .activityChanged, object: nil)
    }
}
