package lk.sentinelmesh.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import lk.sentinelmesh.app.ui.MainScreen
import lk.sentinelmesh.app.ui.Onboarding
import lk.sentinelmesh.app.ui.SentinelTheme

class MainActivity : ComponentActivity() {
    // Bumped on every resume so permission rows re-check after the user returns from Settings.
    private val resumeTick = mutableIntStateOf(0)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            SentinelTheme {
                val onboarded by Sentinel.onboarded.collectAsState()
                if (onboarded) MainScreen(resumeTick.intValue) else Onboarding(resumeTick.intValue)
            }
        }
    }

    override fun onResume() {
        super.onResume()
        Sentinel.appVisible = true
        resumeTick.intValue++
        Sentinel.refreshNow()
        runCatching { ListenerService.start(this) }
    }

    override fun onPause() {
        Sentinel.appVisible = false
        super.onPause()
    }
}
