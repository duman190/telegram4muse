import Foundation
import T4MCore
import TDLibFramework

/// A TDLib client over the JSON interface (td_send / td_receive).
///
/// Requests get a unique "@extra" and are matched to their responses; anything
/// without a waiting request (updates) goes to `updateHandler`, which is called
/// on TDLib's receive thread.
final class TDClient: @unchecked Sendable {
    let clientId: Int32
    private let updateHandler: (TDObject) -> Void
    private let lock = NSLock()
    private var pending: [String: (Result<TDObject, Error>) -> Void] = [:]
    private var nextRequestId: UInt64 = 0

    init(updateHandler: @escaping (TDObject) -> Void) {
        self.updateHandler = updateHandler
        clientId = td_create_client_id()
        Self.register(self)
    }

    /// Sends a request and waits for its response. TDLib errors are thrown as `TDError`.
    func send(_ request: TDObject, timeout: TimeInterval = 30) async throws -> TDObject {
        try await withCheckedThrowingContinuation { continuation in
            send(request, timeout: timeout) { continuation.resume(with: $0) }
        }
    }

    private func send(_ request: TDObject, timeout: TimeInterval, completion: @escaping (Result<TDObject, Error>) -> Void) {
        lock.lock()
        nextRequestId += 1
        let extra = "t4m-\(nextRequestId)"
        pending[extra] = completion
        lock.unlock()

        var request = request
        request["@extra"] = extra
        guard let json = try? TDJSON.encode(request) else {
            finish(extra, .failure(TDError(code: 0, message: "Could not encode the request")))
            return
        }
        td_send(clientId, json)
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout) { [weak self] in
            self?.finish(extra, .failure(TDError(code: 408, message: "Telegram did not answer in time")))
        }
    }

    /// Completes a request at most once (response vs. timeout).
    private func finish(_ extra: String, _ result: Result<TDObject, Error>) {
        lock.lock()
        let completion = pending.removeValue(forKey: extra)
        lock.unlock()
        completion?(result)
    }

    private func receive(_ object: TDObject) {
        if let extra = object["@extra"] as? String, extra.hasPrefix("t4m-") {
            finish(extra, object.tdType == "error" ? .failure(TDError(object)) : .success(object))
            return
        }
        updateHandler(object)
        if object.tdType == "updateAuthorizationState",
           object.object("authorization_state")?.tdType == "authorizationStateClosed" {
            Self.unregister(clientId)
        }
    }

    /// Runs a request synchronously; only for methods TDLib documents as
    /// "Can be called synchronously", like setLogVerbosityLevel.
    static func execute(_ request: TDObject) {
        guard let json = try? TDJSON.encode(request) else { return }
        _ = td_execute(json)
    }

    // MARK: Receive loop
    //
    // td_receive is process-wide and returns events for every client, tagged
    // with "@client_id". One thread reads them all and routes them.

    private static let registryLock = NSLock()
    nonisolated(unsafe) private static var clients: [Int32: TDClient] = [:]
    nonisolated(unsafe) private static var receiverStarted = false

    private static func register(_ client: TDClient) {
        registryLock.lock()
        clients[client.clientId] = client
        let start = !receiverStarted
        receiverStarted = true
        registryLock.unlock()
        if start {
            let thread = Thread { receiveLoop() }
            thread.name = "tdlib-receive"
            thread.qualityOfService = .userInitiated
            thread.start()
        }
    }

    private static func unregister(_ clientId: Int32) {
        registryLock.lock()
        clients.removeValue(forKey: clientId)
        registryLock.unlock()
    }

    private static func receiveLoop() {
        while true {
            guard let raw = td_receive(30.0) else { continue }
            guard let object = TDJSON.decode(String(cString: raw)) else { continue }
            let id = Int32(truncatingIfNeeded: object.int64("@client_id") ?? 0)
            registryLock.lock()
            let client = clients[id]
            registryLock.unlock()
            client?.receive(object)
        }
    }
}
