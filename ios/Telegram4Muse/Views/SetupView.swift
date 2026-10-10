import SwiftUI

/// First run: the Telegram API credentials this app signs in with.
struct SetupView: View {
    @Environment(AppModel.self) private var model
    @State private var apiId = ""
    @State private var apiHash = ""

    var body: some View {
        Form {
            Section {
                Text("Telegram4Muse signs in to Telegram as you, so Muse can post polls in the groups you pick. Your Telegram session never leaves this iPhone.")
            }
            Section {
                TextField("api_id", text: $apiId)
                    .keyboardType(.numberPad)
                TextField("api_hash", text: $apiHash)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.body.monospaced())
                Link("Get them at my.telegram.org", destination: URL(string: "https://my.telegram.org/apps")!)
            } header: {
                Text("Telegram API credentials")
            } footer: {
                Text("Log in at my.telegram.org, open API development tools and create an app. These are stored in this iPhone's Keychain.")
            }
            if let error = model.errorMessage {
                Section {
                    Text(error).foregroundStyle(.red)
                }
            }
            Section {
                Button("Continue") {
                    model.saveTelegramCredentials(apiId: apiId, apiHash: apiHash)
                }
                .disabled(apiId.isEmpty || apiHash.isEmpty)
            }
        }
    }
}
