// swift-tools-version:5.9
// Platform-independent logic shared by the app: job models, the job executor,
// TDLib request builders and parsers, and the bridge HTTP client.
// Runs and tests on Linux too (`swift test`), which keeps CI cheap.
import PackageDescription

let package = Package(
    name: "T4MCore",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "T4MCore", targets: ["T4MCore"]),
    ],
    targets: [
        .target(name: "T4MCore"),
        .testTarget(
            name: "T4MCoreTests",
            dependencies: ["T4MCore"],
            resources: [.copy("Fixtures")]
        ),
    ]
)
