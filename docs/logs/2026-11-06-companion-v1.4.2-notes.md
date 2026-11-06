## Highlights

**companion-v1.4.1 → companion-v1.4.2** · 1 commit (Android-side: 1 change, in the background install prompt)

The background check no longer asks again about a build the user declined when the same app and patches version comes back after a re-patch. **The server fix in the same push takes effect only after the server image is redeployed. The phone stops the repeated prompt only after installing this APK.** The APK asset is named `relume-1.4.2-universal.apk`.

### 🛠 Fixed & Improved

**Android app**
- A declined background build is remembered by its app, app version, patches version and tag, not by the APK sha256. A re-patch of the same versions is no longer asked again after the user declined it.
- A newer build of the same app is still asked, because its version differs.

**Server** (no app action needed)
- A scheduled re-patch with the same app and patches version no longer makes phones prompt for an install. Install happens only when the app version or patches version differs from what the phone holds, or when the phone's version code or signer drifts from its record.

### 📦 Install / Upgrade

Install `relume-1.4.2-universal.apk` over 1.4.1. Signing is unchanged, so no reinstall is needed.
