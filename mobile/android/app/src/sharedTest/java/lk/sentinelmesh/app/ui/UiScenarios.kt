package lk.sentinelmesh.app.ui

import android.graphics.Bitmap
import android.graphics.Canvas
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.runner.lifecycle.ActivityLifecycleMonitorRegistry
import androidx.test.runner.lifecycle.Stage
import lk.sentinelmesh.app.*
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.Before
import kotlinx.coroutines.cancelChildren
import java.io.File

/** Exercise real Compose controls with local fixtures; these tests never issue cloud warnings. */
abstract class UiScenarios {
    @get:Rule val compose = createComposeRule()
    private val s = strings(Lang.EN)
    private val zone = Zone("Z07", "Kelani River · Hanwella", "Colombo", "flood", listOf(listOf(
        LatLon(6.9, 80.1), LatLon(6.9, 80.11), LatLon(6.91, 80.11), LatLon(6.91, 80.1), LatLon(6.9, 80.1),
    )))
    private val now = 1_800_000_000_000L
    private fun message(id: String, tier: String, area: String = "Z07") = AlertMsg(
        id, area, tier, "flood", "Actual", mapOf("en" to "$id instructions"), now, "push", now / 1000,
    )

    @Before fun stopBackgroundRefreshForFixtures() {
        // Isolate the UI from live cloud data. No services are started by the empty test activity.
        Sentinel.scope.coroutineContext.cancelChildren()
        Sentinel.lang.value = Lang.EN
        Sentinel.refreshing.value = false
        Sentinel.refreshFailed.value = false
    }

    @Test fun mainNavigationShowsEveryScreen() {
        Sentinel.zones.value = listOf(zone)
        Sentinel.myZoneId.value = zone.id
        Sentinel.states.value = mapOf(zone.id to ZoneState("warning", false, null))
        Sentinel.statesAt.value = now
        Sentinel.history.value = listOf(message("SM-DEMO", "warning"))
        Sentinel.link.value = Link.OFFLINE
        compose.setContent { SentinelTheme { MainScreen(0) } }
        compose.onNodeWithTag("navigation-0").assertIsSelected()
        compose.onNodeWithText(zone.name).assertIsDisplayed()
        screenshot("home")
        compose.onNodeWithTag("navigation-1").performClick().assertIsSelected()
        screenshot("map")
        compose.onNodeWithTag("navigation-2").performClick().assertIsSelected()
        compose.onNodeWithTag("filter-urgent").assertIsDisplayed()
        compose.onNodeWithTag("filter-urgent").performClick()
        screenshot("history")
        compose.onNodeWithTag("navigation-3").performClick().assertIsSelected()
        compose.onNodeWithTag("language-en").assertIsDisplayed()
        screenshot("settings")
        compose.onNodeWithTag("language-ta").performClick().assertIsSelected()
        compose.runOnIdle { assertEquals(Lang.TA, Sentinel.lang.value) }
        compose.onNodeWithTag("language-en").performClick()
        compose.onNodeWithTag("navigation-2").performClick()
        compose.onNodeWithTag("filter-urgent").assertIsSelected()
    }

    @Test fun languageSelectorChangesSelection() {
        compose.setContent { SentinelTheme {
            var language by remember { mutableStateOf(Lang.EN) }
            LanguageSelector(language, { language = it })
        } }
        compose.onNodeWithTag("language-en").assertIsSelected()
        compose.onNodeWithTag("language-ta").performClick().assertIsSelected()
        compose.onNodeWithTag("language-en").assertIsNotSelected()
        compose.onNodeWithTag("language-si").performClick().assertIsSelected()
    }

    @Test fun alertFiltersCombineUrgencyAndArea() {
        compose.setContent { SentinelTheme {
            AlertHistory(listOf(message("urgent", "warning"), message("clear", "clear"), message("other", "imminent", "Z08")), "Z07", s, Lang.EN)
        } }
        compose.onNodeWithTag("alert-clear").assertExists()
        compose.onNodeWithTag("filter-urgent").performClick()
        compose.onNodeWithTag("alert-clear").assertDoesNotExist()
        compose.onNodeWithTag("alert-other").assertExists()
        compose.onNodeWithTag("filter-area").performClick()
        compose.onNodeWithTag("alert-other").assertDoesNotExist()
        compose.onNodeWithTag("alert-urgent").assertIsDisplayed()
        compose.onNodeWithTag("filter-all").performClick()
        compose.onNodeWithTag("alert-clear").assertExists()
        screenshot("alerts")
    }

    @Test fun areaSearchFindsDistrictAndReportsNoMatches() {
        var picked: Zone? = null
        compose.setContent { SentinelTheme {
            AreaPicker(s, listOf(zone), emptyMap(), null, { picked = it }, Modifier.fillMaxSize())
        } }
        compose.onNodeWithTag("area-search").performTextInput("nowhere")
        compose.onNodeWithText(s.noAreas).assertIsDisplayed()
        compose.onNodeWithTag("area-search").performTextReplacement("Colombo")
        compose.onNodeWithTag("area-Z07").performClick()
        compose.runOnIdle { assertEquals(zone, picked) }
    }

    @Test fun missingStatusNeverSaysNormal() {
        compose.setContent { SentinelTheme { StatusCard(zone, null, 0L, s) } }
        compose.onNodeWithText(s.unknown).assertIsDisplayed()
        compose.onNodeWithText(s.statusUnavailable).assertIsDisplayed()
    }

    @Test fun refreshFailureOffersWorkingRetry() {
        var retries = 0
        compose.setContent { SentinelTheme { RefreshFeedback(true, s) { retries++ } } }
        compose.onNodeWithTag("retry-refresh").performClick()
        compose.runOnIdle { assertEquals(1, retries) }
    }

    @Test fun emergencyCanBeReadAndAcknowledged() {
        var acknowledged = false
        compose.setContent { SentinelTheme { AlertScreen(message("emergency", "imminent")) { acknowledged = true } } }
        compose.onNodeWithText("emergency instructions").assertIsDisplayed()
        screenshot("emergency")
        compose.onNodeWithText(s.understood).performScrollTo().performClick()
        compose.runOnIdle { assertEquals(true, acknowledged) }
    }

    @Test fun statusAndLanguageRenderInAllThreeLanguages() {
        var language by mutableStateOf(Lang.EN)
        compose.setContent { SentinelTheme {
            Scaffold { padding -> Column(Modifier.padding(padding).padding(18.dp)) {
                LanguageSelector(language, { language = it })
                StatusCard(zone, ZoneState("warning", false, null), now, strings(language), Modifier.padding(top = 16.dp))
            } }
        } }
        for (lang in Lang.entries) {
            compose.onNodeWithTag("language-${lang.code}").performClick()
            compose.onAllNodesWithText(strings(lang).tier.getValue("warning")).onFirst().assertIsDisplayed()
            screenshot("status-${lang.code}")
        }
    }

    @Test fun largeTextOnNarrowScreenKeepsStatusAndLanguageReadable() {
        var language by mutableStateOf(Lang.SI)
        compose.setContent { SentinelTheme {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, 1.5f)) {
                Column(Modifier.width(320.dp).verticalScroll(rememberScrollState()).padding(16.dp)) {
                    LanguageSelector(language, { language = it })
                    StatusCard(zone, ZoneState("warning", false, null), now, strings(language), Modifier.padding(top = 16.dp))
                }
            }
        } }
        compose.onNodeWithTag("language-si").assertIsSelected()
        screenshot("large-text-si")
        compose.onNodeWithTag("language-ta").performClick().assertIsSelected()
        compose.onNodeWithText(strings(Lang.TA).tier.getValue("warning")).assertExists()
        screenshot("large-text-ta")
    }

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val localDir = System.getProperty("sentinel.screenshotDir")
        val dir = (localDir?.let { File(it) }
            ?: File(context.getExternalFilesDir(null), "ui-screenshots")).apply { mkdirs() }
        val bitmap = if (localDir != null) {
            // Windows Robolectric cannot complete hardware PixelCopy. Draw the Android decor view
            // using its native software canvas; device tests still capture the composed window.
            var result: Bitmap? = null
            compose.runOnIdle {
                val activity = ActivityLifecycleMonitorRegistry.getInstance().getActivitiesInStage(Stage.RESUMED).first()
                val view = activity.window.decorView
                result = Bitmap.createBitmap(view.width, view.height, Bitmap.Config.ARGB_8888).also { view.draw(Canvas(it)) }
            }
            requireNotNull(result)
        } else compose.onRoot().captureToImage().asAndroidBitmap()
        File(dir, "$name.png").outputStream().use {
            bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)
        }
    }
}
