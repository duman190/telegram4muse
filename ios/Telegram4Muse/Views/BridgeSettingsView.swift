import SwiftUI

/// Where the bridge lives and the device token it expects.
struct BridgeSettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var url = AppSettings.bridgeURL?.absoluteString ?? ""
    @State private var token = ""
    @State private var saving = false

    var body: some View {
        Form {
            Section {
                TextField("https://your-bridge.fly.dev", text: $url)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                SecureField(AppSettings.deviceToken == nil ? "Device token" : "Device token (saved; paste to replace)", text: $token)
            } header: {
                Text("Bridge")
            } footer: {
                Text("The bridge is the small server Muse sends jobs to. Paste its address and the DEVICE_TOKEN you set on it. The token is kept in this iPhone's Keychain.")
            }
            if let error = model.errorMessage {
                Section {
                    Text(error).foregroundStyle(.red)
                }
            } else if let message = model.bridgeMessage {
                Section {
                    Text(message).foregroundStyle(model.bridgeOK ? Color.secondary : Color.red)
                }
            }
            Section {
                Button(saving ? "Connecting…" : "Save and connect", action: save)
                    .disabled(url.isEmpty || saving)
            }
        }
        .navigationTitle("Bridge")
    }

    private func save() {
        saving = true
        Task {
            let ok = await model.saveBridge(url: url, token: token)
            saving = false
            if ok { dismiss() }
        }
    }
}
