package lk.sentinelmesh.app.ui

import android.app.Application
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.unit.Density
import androidx.compose.ui.unit.dp
import lk.sentinelmesh.app.AlertMsg
import lk.sentinelmesh.app.Lang
import lk.sentinelmesh.app.strings
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

/** Representative large Samsung phone viewport; no physical device or cloud services needed. */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w412dp-h915dp-xhdpi", application = Application::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class AlignmentTest {
    @get:Rule val compose = createComposeRule()

    @Test fun longButtonCanGrowWithLargeTamilText() {
        val text = strings(Lang.TA).useMyLocation
        compose.setContent { SentinelTheme {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, 2f)) {
                Column(Modifier.fillMaxWidth().padding(20.dp)) {
                    PrimaryButton(text, {}, Modifier.fillMaxWidth())
                }
            }
        } }
        compose.onNodeWithText(text).assertIsDisplayed()
        val layout = mutableListOf<androidx.compose.ui.text.TextLayoutResult>()
        compose.onNodeWithText(text).performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.GetTextLayoutResult) { it(layout) }
        assertTrue("Button label must not clip: ${layout.map { "size=${it.size}, lines=${it.lineCount}, widthOverflow=${it.didOverflowWidth}, heightOverflow=${it.didOverflowHeight}" }}", layout.none { it.hasVisualOverflow })
    }

    @Test fun alertBadgeAndTimestampStayReadableWithLargeSinhalaText() {
        val s = strings(Lang.SI)
        val message = AlertMsg("alignment", "Z07", "imminent", "flood", "Exercise", mapOf("si" to "Test instructions"), 1800000000000L, "test")
        compose.setContent { SentinelTheme {
            val density = LocalDensity.current
            CompositionLocalProvider(LocalDensity provides Density(density.density, 1.5f)) {
                Column(Modifier.fillMaxWidth().padding(18.dp)) { Card(Modifier.fillMaxWidth()) { AlertRow(message, s, Lang.SI) } }
            }
        } }
        compose.onNodeWithText(s.headline.getValue("imminent")).assertIsDisplayed()
        compose.onNodeWithText(s.exercise).assertIsDisplayed()
        compose.onNodeWithText(clock(message.receivedAt), substring = true).assertIsDisplayed()
    }
}
