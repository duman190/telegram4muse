import Foundation
import LocalAuthentication
import Observation

/// Face ID gate for the app's screens. It protects what a person holding your
/// unlocked phone could do in this app (sign in, change allowed groups); it
/// doesn't block background jobs, which need to run while the phone is locked.
@MainActor
@Observable
final class AppLock {
    private(set) var isUnlocked = false
    private(set) var error: String?
    private var isPrompting = false

    func unlock() async {
        guard !isUnlocked, !isPrompting else { return }
        isPrompting = true
        defer { isPrompting = false }
        let context = LAContext()
        var policyError: NSError?
        // Face ID, falling back to the passcode if Face ID fails or is unavailable.
        guard context.canEvaluatePolicy(.deviceOwnerAuthentication, error: &policyError) else {
            error = "Turn on Face ID or a passcode for this iPhone to use Telegram4Muse."
            return
        }
        do {
            isUnlocked = try await context.evaluatePolicy(.deviceOwnerAuthentication,
                                                          localizedReason: "Unlock your Telegram connection for Muse")
            error = nil
        } catch let laError as LAError where laError.code == .userCancel || laError.code == .appCancel || laError.code == .systemCancel {
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }

    func lock() {
        isUnlocked = false
    }
}
