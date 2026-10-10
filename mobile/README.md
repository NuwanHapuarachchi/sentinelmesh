# SentinelMesh mobile app

The existing `main` branch contains a native Kotlin / Jetpack Compose Android app in `android/`.
Development continues on `feature/mobile-app`. It serves residents using the same public zone data
and warning broadcasts as the web application. Android 8.0 (API 26) or newer is required.

## Included

- Sinhala, Tamil and English onboarding and in-app language switching.
- Searchable area selection and optional on-device location matching.
- Home guidance, zone map, alert history and notification permission settings.
- A foreground listener using a Cognito guest identity and AWS IoT MQTT over WebSocket.
- Full-screen emergency alerts, subject to the device's notification and full-screen permissions.
- Catch-up from the area's latest broadcast, with a six-hour expiry check on both delivery paths.
- Cached zone geometry, last downloaded zone status and the latest 50 alerts for offline viewing.
- A clearly marked local exercise under Settings → Test alarm.

The app receives internet broadcasts; it does not implement carrier cell broadcast or read the phone's
SMS inbox. It does not yet include the web phone's optional SMS opt-in/OTP flow or officer console.
Without internet it can display saved information but cannot receive new cloud warnings.
Missing status is shown as unavailable. Saved status retains its last download time.

## Build and run

Open `mobile/android` in Android Studio and let Gradle sync. Use JDK 17 or the compatible bundled
Android Studio JDK, Android SDK platform 36 and build tools 36.0.0. Set the local SDK path in the
ignored `local.properties`, or set `ANDROID_HOME`.

From `mobile/android` on Windows:

```powershell
./gradlew.bat assembleDebug testDebugUnitTest lintDebug --console=plain
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

On Linux/macOS use `./gradlew` instead. Launch SentinelMesh, choose a language and area, then
allow notifications. Check the full-screen and background settings and try the local test alarm.

The default deployment URL is defined in `android/app/build.gradle.kts`. To target another deployed
SentinelMesh stack, pass its HTTPS CloudFront base URL including the trailing slash:

```powershell
./gradlew.bat assembleDebug -PsentinelBaseUrl=https://YOUR-DISTRIBUTION.cloudfront.net/
```

That deployment must serve `config.json` with `region`, `identity_pool_id` and `iot_endpoint`,
plus `zones.geojson`, `public/zones.json` and `cb/{zone}.json`. The guest identity and listen-only
permissions are defined in `terraform/app/mobile.tf`. Use the repository's existing deployment
pipeline to provision these resources.

## Verification

Unit tests cover multipart area matching, outside-coverage selection, alert expiry, status validation
and cross-zone rejection. Shared Compose tests exercise navigation, combined alert filters, language
selection, area search, unavailable status, refresh retry and emergency acknowledgement. They run
locally with Robolectric's Android 14 native graphics runtime during `testDebugUnitTest`; the same
scenarios can run on an emulator or connected Android device with `connectedDebugAndroidTest`.
Screenshots from local tests are saved in `android/app/build/reports/ui-screenshots/`; device test
screenshots are saved in the app's external files under `ui-screenshots/`.

The app follows the web console's colours and bundles its Barlow Condensed and Source Sans 3 fonts
for consistent offline rendering. Font licenses are in `mobile/licenses/`. Material 3 navigation,
segmented language selection, filter chips, pull-to-refresh, scrollable onboarding and searchable map
selection provide native controls. Cached status always includes its download time.

The Android CI job builds `sentinelmesh.apk`, compiles device UI tests, runs local UI/unit tests and
lint, and publishes JUnit results. Release APKs currently use the debug signing key for demo installation;
store distribution needs a dedicated signing configuration.

On a device, verify each language, permissions denied/allowed, a locked-screen test alarm, a real
warning for the selected zone, an unrelated-zone warning, offline/reconnect catch-up, area changes
and reboot recovery. Do not infer live end-to-end delivery from a successful APK build.

There is no iOS project in the repository yet.
