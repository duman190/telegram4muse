import SwiftUI
import T4MCore

/// Telegram sign-in: phone number, login code, and 2-step password if set.
struct SignInView: View {
    @Environment(AppModel.self) private var model
    @State private var input = ""

    var body: some View {
        Form {
            Section {
                Text(prompt)
            } header: {
                Text("Sign in to Telegram")
            }
            if model.authState.needsInput {
                Section {
                    field
                        .onSubmit(submit)
                    Button(action: submit) {
                        if model.isBusy {
                            ProgressView()
                        } else {
                            Text("Continue")
                        }
                    }
                    .disabled(input.isEmpty || model.isBusy)
                }
            }
            if let error = model.errorMessage {
                Section {
                    Text(error).foregroundStyle(.red)
                }
            }
            if model.authState != .waitPhoneNumber {
                Section {
                    Button("Start over", role: .destructive) {
                        Task { await model.signOut() }
                    }
                }
            }
        }
        .onChange(of: model.authState) {
            input = ""
        }
    }

    private func submit() {
        let value = input
        Task { await model.submitSignIn(value) }
    }

    private var prompt: String {
        switch model.authState {
        case .waitPhoneNumber:
            return "Enter your phone number with the country code."
        case .waitCode(let phone, let delivery):
            return "Telegram sent a login code \(delivery) to \(phone)."
        case .waitPassword(let hint):
            return hint.isEmpty ? "Enter your two-step verification password." : "Enter your two-step verification password (hint: \(hint))."
        case .waitEmailAddress:
            return "Telegram wants an email address for login codes on this account."
        case .waitEmailCode(let pattern):
            return "Enter the code Telegram emailed to \(pattern)."
        case .unsupported(let message), .failed(let message):
            return message
        default:
            return ""
        }
    }

    @ViewBuilder
    private var field: some View {
        switch model.authState {
        case .waitPhoneNumber:
            TextField("+1 415 555 0100", text: $input)
                .keyboardType(.phonePad)
                .textContentType(.telephoneNumber)
        case .waitCode, .waitEmailCode:
            TextField("Code", text: $input)
                .keyboardType(.numberPad)
                .textContentType(.oneTimeCode)
        case .waitPassword:
            SecureField("Password", text: $input)
                .textContentType(.password)
        case .waitEmailAddress:
            TextField("you@example.com", text: $input)
                .keyboardType(.emailAddress)
                .textContentType(.emailAddress)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
        default:
            EmptyView()
        }
    }
}
