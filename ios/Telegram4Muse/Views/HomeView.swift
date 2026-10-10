import SwiftUI
import T4MCore

/// Signed in: who you are, what Muse may touch, bridge status, recent activity.
struct HomeView: View {
    @Environment(AppModel.self) private var model
    @State private var confirmSignOut = false

    var body: some View {
        List {
            Section("Telegram") {
                LabeledContent("Signed in as", value: model.accountName ?? "…")
            }

            Section {
                if model.allowedChats.isEmpty {
                    Text("None yet. Muse can't post anywhere until you allow a group.")
                        .foregroundStyle(.secondary)
                }
                ForEach(model.allowedChats) { chat in
                    Label(chat.title, systemImage: chat.kind == .channel ? "megaphone" : "person.3")
                }
                NavigationLink("Choose groups") {
                    GroupPickerView()
                }
            } header: {
                Text("Groups Muse can use")
            } footer: {
                Text("In these groups only, Muse can post polls and messages as you, pin and unpin them, close polls and read poll results. Nothing else in your Telegram is reachable.")
            }

            Section {
                LabeledContent("Bridge", value: model.bridgeHost ?? "Not set")
                if let message = model.bridgeMessage {
                    Text(message)
                        .font(.footnote)
                        .foregroundStyle(model.bridgeOK ? Color.secondary : Color.red)
                }
                NavigationLink("Bridge settings") {
                    BridgeSettingsView()
                }
                Button("Run waiting jobs now") {
                    Task { await model.runPendingNow() }
                }
            } header: {
                Text("Muse bridge")
            }

            Section {
                Toggle("Notify me when Muse posts", isOn: Binding(
                    get: { model.notifyOnJobs },
                    set: { model.setNotifyOnJobs($0) }
                ))
            }

            Section("Recent activity") {
                if model.activity.isEmpty {
                    Text("Nothing yet.").foregroundStyle(.secondary)
                }
                ForEach(model.activity) { entry in
                    ActivityRow(entry: entry)
                }
            }

            Section {
                Button("Sign out of Telegram", role: .destructive) {
                    confirmSignOut = true
                }
            }
        }
        .refreshable { await model.refresh() }
        .confirmationDialog("Sign out of Telegram?", isPresented: $confirmSignOut, titleVisibility: .visible) {
            Button("Sign out", role: .destructive) {
                Task { await model.signOut() }
            }
        } message: {
            Text("Muse won't be able to post until you sign in again, and your allowed groups are cleared.")
        }
    }
}

struct ActivityRow: View {
    let entry: ActivityEntry

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Image(systemName: entry.succeeded ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                .foregroundStyle(entry.succeeded ? Color.green : Color.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.summary)
                    .font(.subheadline)
                Text(entry.date.formatted(.relative(presentation: .named)))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }
}
