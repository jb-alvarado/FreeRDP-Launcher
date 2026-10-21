# Model checks

From the project directory, with the macOS command-line tools installed:

```sh
mkdir -p build/checks
xcrun swiftc -parse-as-library -target "$(uname -m)-apple-macos13.0" \
  -module-cache-path build/checks/module-cache \
  -framework AppKit -framework Security \
  Sources/FreeRDPLauncher/ConnectionSettings.swift \
  Sources/FreeRDPLauncher/Localization.swift \
  Sources/FreeRDPLauncher/PasswordStore.swift \
  Sources/FreeRDPLauncher/MemoryLogStore.swift \
  Sources/FreeRDPLauncher/LauncherModel.swift \
  Tests/Checks.swift -o build/checks/checks
build/checks/checks
```

Uses temporary preference domains, does not read actual credentials or connect to a server.
Checks normal SDL exits versus authentication/signal failures, migration, profile creation/selection/relaunch, optional domains and domain-specific credential identities, optional display values, password separation, fragmented UTF-8 logs,
ANSI removal, password redaction, oversized lines and ring-buffer limits.

Manual app checks: switch language in the FreeRDP app menu and relaunch; select automatic
system language. Close the SDL window and verify the launcher reports a normal session end.
Create two connections, rename one and leave the other name empty; verify dropdown labels
and domain/display settings survive switching and relaunch. Verify the password field clears
on profile switches. Open logs using Command-Shift-L,
connect/disconnect, close/reopen the log window while output continues, turn Follow output
off/on, resize the window and scroll horizontally through a long line. Switch the macOS
appearance with the app open and verify text, background and log severity colors.

Log-view regression check (AppKit view only; no connection or credentials):

```sh
xcrun swiftc -parse-as-library -target "$(uname -m)-apple-macos13.0" \
  -module-cache-path build/checks/module-cache \
  -framework SwiftUI -framework AppKit -framework Security \
  Sources/FreeRDPLauncher/ConnectionSettings.swift \
  Sources/FreeRDPLauncher/Localization.swift \
  Sources/FreeRDPLauncher/PasswordStore.swift \
  Sources/FreeRDPLauncher/MemoryLogStore.swift \
  Sources/FreeRDPLauncher/LauncherModel.swift \
  Sources/FreeRDPLauncher/LogWindow.swift \
  Tests/LogViewChecks.swift -o build/checks/log-view-checks
build/checks/log-view-checks
```
