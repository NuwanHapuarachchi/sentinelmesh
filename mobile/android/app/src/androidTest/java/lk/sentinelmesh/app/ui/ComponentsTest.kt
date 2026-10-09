package lk.sentinelmesh.app.ui

import android.graphics.Bitmap
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Scaffold
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.dp
import androidx.test.platform.app.InstrumentationRegistry
import lk.sentinelmesh.app.*
import org.junit.Assert.assertEquals
import org.junit.Rule
import org.junit.Test
import org.junit.Before
import kotlinx.coroutines.cancelChildren
import java.io.File

/** Exercise real Compose controls with local fixtures; these tests never issue cloud warnings. */
class ComponentsTest {
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
        screenshot("history")
        compose.onNodeWithTag("navigation-3").performClick().assertIsSelected()
        compose.onNodeWithTag("language-en").assertIsDisplayed()
        screenshot("settings")
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

    private fun screenshot(name: String) {
        compose.waitForIdle()
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val dir = File(context.getExternalFilesDir(null), "ui-screenshots").apply { mkdirs() }
        File(dir, "$name.png").outputStream().use {
            compose.onRoot().captureToImage().asAndroidBitmap().compress(Bitmap.CompressFormat.PNG, 100, it)
        }
    }
}
