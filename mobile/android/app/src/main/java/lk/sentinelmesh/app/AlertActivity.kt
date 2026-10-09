package lk.sentinelmesh.app

import android.content.Intent
import android.os.Bundle
import android.os.Build
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import lk.sentinelmesh.app.ui.SentinelTheme
import lk.sentinelmesh.app.ui.clock
import lk.sentinelmesh.app.ui.hazardOf
import lk.sentinelmesh.app.ui.tierColor
import org.json.JSONObject

/** The full-screen alarm: over the lock screen, in the person's language, until they acknowledge it. */
class AlertActivity : ComponentActivity() {
    private val alert = mutableStateOf<AlertMsg?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON)
        }
        load(intent)
        setContent { SentinelTheme { alert.value?.let { AlertScreen(it) { dismiss() } } } }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        load(intent)
    }

    private fun load(i: Intent) {
        val raw = i.getStringExtra(Alarm.EXTRA_ALERT) ?: run { finish(); return }
        alert.value = AlertMsg.parse(JSONObject(raw))
    }

    private fun dismiss() {
        Alarm.silence(this)
        finish()
    }
}

@Composable
internal fun AlertScreen(m: AlertMsg, onAck: () -> Unit) {
    val lang by Sentinel.lang.collectAsState()
    val s = strings(lang)
    val zone = Sentinel.zone(m.zoneId)
    val base = tierColor(m.tier)
    val pulse = rememberInfiniteTransition(label = "p").animateFloat(0.92f, 1.08f, infiniteRepeatable(tween(700), RepeatMode.Reverse), label = "s")
    Box(Modifier.fillMaxSize().background(base)) {
        Column(
            Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().verticalScroll(rememberScrollState()).padding(24.dp),
        ) {
            Spacer(Modifier.height(24.dp))
            Box(Modifier.size(92.dp).scale(pulse.value).clip(CircleShape).background(Color(0x33FFFFFF)), contentAlignment = Alignment.Center) {
                Box(Modifier.size(66.dp).clip(CircleShape).background(Color.White), contentAlignment = Alignment.Center) {
                    Icon(Icons.Filled.Warning, null, tint = base, modifier = Modifier.size(40.dp))
                }
            }
            Spacer(Modifier.height(22.dp))
            if (m.status != "Actual") Text(s.exercise.uppercase(), color = Color(0xFFFFE08A), style = MaterialTheme.typography.labelLarge)
            Text(s.headline[m.tier] ?: m.tier, color = Color.White, style = MaterialTheme.typography.displaySmall)
            Spacer(Modifier.height(6.dp))
            Text(
                listOfNotNull(zone?.name, zone?.district, s.hazardOf(m.hazard)).joinToString(" · "),
                color = Color(0xE6FFFFFF), style = MaterialTheme.typography.titleMedium,
            )
            Spacer(Modifier.height(22.dp))
            Box(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Color(0x26FFFFFF)).padding(20.dp)) {
                Text(m.text(lang), color = Color.White, style = MaterialTheme.typography.titleLarge)
            }
            Spacer(Modifier.height(18.dp))
            Text(s.whatToDo.uppercase(), color = Color(0xCCFFFFFF), style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(8.dp))
            (s.guidance[m.tier] ?: emptyList()).forEach { line ->
                Row(Modifier.padding(vertical = 5.dp), verticalAlignment = Alignment.Top) {
                    Box(Modifier.padding(top = 8.dp).size(8.dp).clip(CircleShape).background(Color.White))
                    Spacer(Modifier.width(12.dp))
                    Text(line, color = Color.White, style = MaterialTheme.typography.bodyLarge, fontSize = 18.sp)
                }
            }
            Spacer(Modifier.height(16.dp))
            Text("${s.receivedAt} ${clock(m.receivedAt)}", color = Color(0xB3FFFFFF), style = MaterialTheme.typography.labelMedium)
            Spacer(Modifier.height(28.dp))
            Button(
                onClick = onAck, modifier = Modifier.fillMaxWidth().height(62.dp), shape = RoundedCornerShape(10.dp),
                colors = ButtonDefaults.buttonColors(containerColor = Color.White, contentColor = base),
            ) { Text(s.understood, fontSize = 20.sp, fontWeight = FontWeight.Bold) }
            Spacer(Modifier.height(8.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center) {
                Text("SentinelMesh", color = Color(0x99FFFFFF), style = MaterialTheme.typography.labelMedium)
            }
        }
    }
}
