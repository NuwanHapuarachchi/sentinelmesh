# Mobile UI review

The Android screens now share the web console's river-blue header, mist background, white bordered
panels, risk colours, Barlow Condensed headings and Source Sans 3 body text. Fonts are bundled so
typography remains available offline.

Native Material 3 controls provide navigation, refresh feedback, pull-to-refresh, language selection,
alert filtering, searchable area selection and actionable permission settings. Filters survive tab
changes. On narrow screens with enlarged text, language options stack vertically and status labels
wrap as complete controls. Onboarding and alert instructions scroll.

## Validation

- Debug and demo release APK builds pass.
- All 15 local tests pass: 6 alert/location tests and 9 Compose UI scenarios on Android 14 through Robolectric.
- Device instrumentation tests compile from the same shared UI scenarios.
- Lint passes with warnings, principally dependency updates and existing Android configuration/style recommendations.
- Screenshots were rendered and visually inspected, including Sinhala/Tamil and 320 dp layouts at 150% font size.

The screenshots below use deterministic local warning fixtures. They are UI review artifacts, not
evidence of delivery from the deployed backend. The map fixture contains one simple polygon and
does not require downloaded street tiles. Android hardware PixelCopy timed out in the Windows test
runtime, so local screenshots use native software drawing of the Android decor view. Device tests
use normal window capture. Emulator downloads did not finish; locked-screen behaviour, sound,
reboot recovery and live AWS delivery still need verification on a device.

| Screen | Preview |
|---|---|
| Home | [Home](screenshots/home.png) |
| Map | [Map](screenshots/map.png) |
| Alert filters | [History](screenshots/history.png) |
| Language and permissions | [Settings](screenshots/settings.png) |
| Emergency instructions | [Emergency](screenshots/emergency.png) |
| Sinhala status | [Sinhala](screenshots/status-si.png) |
| Tamil status | [Tamil](screenshots/status-ta.png) |
| Enlarged Sinhala text | [150% Sinhala](screenshots/large-text-si.png) |
| Enlarged Tamil text | [150% Tamil](screenshots/large-text-ta.png) |

Run from `mobile/android`:

```powershell
./gradlew.bat testDebugUnitTest assembleDebug assembleRelease assembleDebugAndroidTest lintDebug --console=plain
```

With a connected device or running emulator, execute `./gradlew.bat connectedDebugAndroidTest`.
The shared tests verify selection changes, combined urgency/area filters, preserved tab state,
search results and empty states, refresh retry, emergency acknowledgement and multilingual layouts.
