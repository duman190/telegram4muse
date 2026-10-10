import SwiftUI
import T4MCore

/// Pick which groups Muse may use. Changes apply on this phone immediately and
/// are shared with the bridge so Muse knows the chat ids.
struct GroupPickerView: View {
    @Environment(AppModel.self) private var model
    @State private var search = ""

    var body: some View {
        List {
            Section {
                ForEach(filtered) { chat in
                    Toggle(isOn: Binding(
                        get: { model.isAllowed(chat) },
                        set: { model.setAllowed(chat, $0) }
                    )) {
                        VStack(alignment: .leading) {
                            Text(chat.title)
                            Text(chat.kind.label)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            } footer: {
                Text("Only groups and channels are listed. Private chats are never available to Muse.")
            }
        }
        .overlay {
            if model.isLoadingGroups && model.availableGroups.isEmpty {
                ProgressView("Loading your groups…")
            }
        }
        .searchable(text: $search)
        .navigationTitle("Allowed groups")
        .task { await model.loadGroups() }
        .refreshable { await model.loadGroups() }
    }

    private var filtered: [TelegramChat] {
        guard !search.isEmpty else { return model.availableGroups }
        return model.availableGroups.filter { $0.title.localizedCaseInsensitiveContains(search) }
    }
}
