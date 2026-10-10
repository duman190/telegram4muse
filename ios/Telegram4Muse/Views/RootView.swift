import SwiftUI
import T4MCore

struct RootView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        Group {
            if model.lock.isUnlocked {
                MainView()
            } else {
                LockView()
            }
        }
        // Hide everything in the app switcher.
        .overlay {
            if scenePhase != .active && model.lock.isUnlocked {
                PrivacyCover()
            }
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: model.didBecomeActive()
            case .background: model.didEnterBackground()
            default: break
            }
        }
    }
}

struct MainView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        NavigationStack {
            content
                .navigationTitle("Telegram4Muse")
        }
    }

    @ViewBuilder
    private var content: some View {
        if !model.hasTelegramCredentials {
            SetupView()
        } else {
            switch model.authState {
            case .ready:
                HomeView()
            case .notConfigured, .starting, .closed, .loggingOut:
                ProgressView("Connecting to Telegram…")
            default:
                SignInView()
            }
        }
    }
}

struct LockView: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        VStack(spacing: 20) {
            Image(systemName: "lock.shield")
                .font(.system(size: 56))
                .foregroundStyle(.tint)
            Text("Telegram4Muse is locked")
                .font(.title2.bold())
            Text("Your Telegram session stays on this iPhone.")
                .foregroundStyle(.secondary)
            Button {
                Task { await model.lock.unlock() }
            } label: {
                Label("Unlock", systemImage: "faceid")
            }
            .buttonStyle(.borderedProminent)
            if let error = model.lock.error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .multilineTextAlignment(.center)
            }
        }
        .padding()
        .task { await model.lock.unlock() }
    }
}

struct PrivacyCover: View {
    var body: some View {
        ZStack {
            Rectangle().fill(.background)
            Image(systemName: "lock.fill")
                .font(.largeTitle)
                .foregroundStyle(.secondary)
        }
        .ignoresSafeArea()
    }
}
