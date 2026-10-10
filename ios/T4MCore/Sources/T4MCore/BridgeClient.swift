import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public struct BridgeError: Error, LocalizedError, Equatable {
    public let status: Int
    public let message: String

    public init(status: Int, message: String) {
        self.status = status
        self.message = message
    }

    public var errorDescription: String? {
        status == 0 ? message : "Bridge error \(status): \(message)"
    }
}

/// An allowed chat as published to the bridge, so Muse can see what it may use.
public struct PublishedChat: Codable, Equatable, Sendable {
    public var id: Int64
    public var title: String
    public var type: String

    public init(id: Int64, title: String, type: String) {
        self.id = id
        self.title = title
        self.type = type
    }
}

/// The phone's side of the bridge API (the /v1/device/... endpoints).
public struct BridgeClient: Sendable {
    public let baseURL: URL
    public let deviceToken: String
    let session: URLSession

    public init(baseURL: URL, deviceToken: String, session: URLSession = .shared) {
        self.baseURL = baseURL
        self.deviceToken = deviceToken
        self.session = session
    }

    struct Registration: Encodable {
        var apnsToken: String?
        var apnsEnv: String
        var appVersion: String
    }

    struct ChatList: Encodable {
        var chats: [PublishedChat]
    }

    public func register(apnsToken: String?, sandbox: Bool, appVersion: String) async throws {
        let body = Registration(apnsToken: apnsToken, apnsEnv: sandbox ? "sandbox" : "production", appVersion: appVersion)
        _ = try await call("POST", "v1/device/register", body: body)
    }

    public func publishChats(_ chats: [PublishedChat]) async throws {
        _ = try await call("PUT", "v1/device/chats", body: ChatList(chats: chats))
    }

    public func claim() async throws -> [BridgeJob] {
        let data = try await call("POST", "v1/device/claim", body: [String: String]())
        return try BridgeCoding.decoder().decode(ClaimResponse.self, from: data).jobs
    }

    public func report(jobId: String, _ report: JobReport) async throws {
        _ = try await call("POST", "v1/device/jobs/\(jobId)/result", body: report)
    }

    private func call<Body: Encodable>(_ method: String, _ path: String, body: Body) async throws -> Data {
        var request = URLRequest(url: baseURL.appendingPathComponent(path))
        request.httpMethod = method
        request.timeoutInterval = 15
        request.setValue("Bearer \(deviceToken)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try BridgeCoding.encoder().encode(body)

        let (data, response): (Data, URLResponse) = try await withCheckedThrowingContinuation { continuation in
            session.dataTask(with: request) { data, response, error in
                if let error {
                    continuation.resume(throwing: BridgeError(status: 0, message: error.localizedDescription))
                } else if let data, let response {
                    continuation.resume(returning: (data, response))
                } else {
                    continuation.resume(throwing: BridgeError(status: 0, message: "Empty response from the bridge"))
                }
            }.resume()
        }
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard (200..<300).contains(status) else {
            throw BridgeError(status: status, message: Self.errorMessage(from: data) ?? HTTPURLResponse.localizedString(forStatusCode: status))
        }
        return data
    }

    static func errorMessage(from data: Data) -> String? {
        struct Envelope: Decodable {
            struct Inner: Decodable { var message: String }
            var error: Inner
        }
        return (try? JSONDecoder().decode(Envelope.self, from: data))?.error.message
    }
}
