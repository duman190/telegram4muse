import Foundation

/// A TDLib JSON object, e.g. `["@type": "getMe"]`.
public typealias TDObject = [String: Any]

/// An error returned by TDLib (`{"@type": "error", "code": ..., "message": ...}`).
public struct TDError: Error, LocalizedError, Equatable {
    public let code: Int
    public let message: String

    public init(code: Int, message: String) {
        self.code = code
        self.message = message
    }

    public init(_ object: TDObject) {
        self.init(code: object.int("code") ?? 0, message: object.string("message") ?? "Unknown Telegram error")
    }

    public var errorDescription: String? { "\(message) (\(code))" }
}

public extension Dictionary where Key == String, Value == Any {
    var tdType: String? { self["@type"] as? String }

    func string(_ key: String) -> String? { self[key] as? String }

    /// TDLib sends int53 values as JSON numbers and int64 values as strings.
    func int64(_ key: String) -> Int64? {
        switch self[key] {
        case let v as Int64: return v
        case let v as Int: return Int64(v)
        case let v as Int32: return Int64(v)
        case let v as NSNumber: return v.int64Value
        case let v as String: return Int64(v)
        default: return nil
        }
    }

    func int(_ key: String) -> Int? { int64(key).map { Int($0) } }

    func bool(_ key: String) -> Bool {
        switch self[key] {
        case let v as Bool: return v
        case let v as NSNumber: return v.boolValue
        default: return false
        }
    }

    func object(_ key: String) -> TDObject? { self[key] as? TDObject }

    func objects(_ key: String) -> [TDObject] { (self[key] as? [Any])?.compactMap { $0 as? TDObject } ?? [] }

    func int64s(_ key: String) -> [Int64] {
        ((self[key] as? [Any]) ?? []).compactMap { value in
            switch value {
            case let v as Int64: return v
            case let v as Int: return Int64(v)
            case let v as NSNumber: return v.int64Value
            default: return nil
            }
        }
    }
}

public enum TDJSON {
    public static func encode(_ object: TDObject) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: object)
        return String(decoding: data, as: UTF8.self)
    }

    public static func decode(_ string: String) -> TDObject? {
        guard let data = string.data(using: .utf8) else { return nil }
        return (try? JSONSerialization.jsonObject(with: data)) as? TDObject
    }
}
