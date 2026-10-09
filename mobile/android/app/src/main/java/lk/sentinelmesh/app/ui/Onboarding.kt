package lk.sentinelmesh.app.ui

import android.Manifest
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.animation.AnimatedContent
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch
import lk.sentinelmesh.app.Lang
import lk.sentinelmesh.app.R
import lk.sentinelmesh.app.Sentinel
import lk.sentinelmesh.app.strings

@Composable
fun Onboarding(resumeTick: Int) {
    val lang by Sentinel.lang.collectAsState()
    val s = strings(lang)
    var step by remember { mutableIntStateOf(0) }
    val zones by Sentinel.zones.collectAsState()
    val states by Sentinel.states.collectAsState()
    val myZone by Sentinel.myZoneId.collectAsState()

    Column(Modifier.fillMaxSize().background(C.mist)) {
        // Brand header
        Box(
            Modifier.fillMaxWidth()
                .background(Brush.verticalGradient(listOf(C.ink, Color(0xFF1C4A68))))
                .statusBarsPadding().padding(horizontal = 24.dp, vertical = 22.dp),
        ) {
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Image(painterResource(R.drawable.ic_launcher_foreground), null, Modifier.size(52.dp).clip(RoundedCornerShape(14.dp)).background(Color(0x22FFFFFF)))
                    Spacer(Modifier.width(12.dp))
                    Column {
                        Text("SentinelMesh", color = Color.White, style = MaterialTheme.typography.headlineSmall)
                        Text(s.tagline, color = Color(0xCCFFFFFF), style = MaterialTheme.typography.bodyMedium)
                    }
                }
                Spacer(Modifier.height(18.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    repeat(3) { i ->
                        Box(Modifier.height(5.dp).weight(1f).clip(RoundedCornerShape(3.dp)).background(if (i <= step) Color.White else Color(0x44FFFFFF)))
                    }
                }
            }
        }
        AnimatedContent(step, label = "step", modifier = Modifier.weight(1f)) { st ->
            when (st) {
                0 -> LanguageStep(lang) { step = 1 }
                1 -> Column(Modifier.fillMaxSize().padding(horizontal = 20.dp)) {
                    Spacer(Modifier.height(20.dp))
                    Text(s.yourArea, style = MaterialTheme.typography.displaySmall)
                    Text(s.yourAreaHint, style = MaterialTheme.typography.bodyLarge, color = C.inkSoft)
                    Spacer(Modifier.height(14.dp))
                    if (zones.isEmpty()) {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(s.connecting, color = C.inkSoft)
                            TextButton(onClick = { Sentinel.scope.launchLoad() }) { Icon(Icons.Filled.Refresh, null) }
                        }
                    }
                    AreaPicker(s, zones, states, myZone, onPick = { Sentinel.setZone(it.id); step = 2 }, modifier = Modifier.weight(1f))
                }
                else -> PermissionsStep(resumeTick)
            }
        }
    }
}

private fun kotlinx.coroutines.CoroutineScope.launchLoad() {
    launch { runCatching { Sentinel.loadStatic() } }
}

@Composable
private fun LanguageStep(current: Lang, next: () -> Unit) {
    val s = strings(current)
    Column(Modifier.fillMaxSize().padding(20.dp).navigationBarsPadding()) {
        Text("භාෂාව · மொழி · Language", style = MaterialTheme.typography.labelLarge, color = C.inkSoft)
        Spacer(Modifier.height(4.dp))
        Text(s.chooseLanguage, style = MaterialTheme.typography.displaySmall)
        Spacer(Modifier.height(20.dp))
        Lang.entries.forEach { l ->
            val sel = l == current
            Row(
                Modifier.fillMaxWidth().padding(vertical = 6.dp).clip(RoundedCornerShape(20.dp))
                    .background(if (sel) C.ink else C.paper)
                    .border(1.dp, if (sel) C.ink else C.rule, RoundedCornerShape(20.dp))
                    .clickable { Sentinel.setLang(l) }.padding(horizontal = 20.dp, vertical = 20.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(l.native, style = MaterialTheme.typography.headlineSmall, color = if (sel) Color.White else C.ink, modifier = Modifier.weight(1f))
                if (sel) Icon(Icons.Filled.Check, null, tint = Color.White)
            }
        }
        Spacer(Modifier.weight(1f))
        PrimaryButton(s.continueLabel, next, Modifier.fillMaxWidth())
    }
}

@Composable
private fun PermissionsStep(resumeTick: Int) {
    val ctx = LocalContext.current
    val s = strings(Sentinel.lang.collectAsState().value)
    var tick by remember { mutableIntStateOf(0) }
    val notify = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { tick++ }
    @Suppress("UNUSED_VARIABLE") val refresh = resumeTick + tick
    Column(Modifier.fillMaxSize().padding(20.dp).navigationBarsPadding()) {
        Text(s.stayProtected, style = MaterialTheme.typography.displaySmall)
        Text(s.stayProtectedHint, style = MaterialTheme.typography.bodyLarge, color = C.inkSoft)
        Spacer(Modifier.height(18.dp))
        PermissionRow(Icons.Filled.Notifications, s.permNotifications, s.permNotificationsHint, notificationsAllowed(ctx), s) {
            if (Build.VERSION.SDK_INT >= 33) notify.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
        PermissionRow(Icons.Filled.Warning, s.permFullScreen, s.permFullScreenHint, fullScreenAllowed(ctx), s) { openFullScreenSettings(ctx) }
        PermissionRow(Icons.Filled.Refresh, s.permBackground, s.permBackgroundHint, backgroundAllowed(ctx), s) { openBatterySettings(ctx) }
        Spacer(Modifier.weight(1f))
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.privacy)
            Spacer(Modifier.height(4.dp))
            Text(s.privacyText, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
        }
        Spacer(Modifier.height(14.dp))
        PrimaryButton(s.start, { Sentinel.finishOnboarding() }, Modifier.fillMaxWidth(), color = C.river)
    }
}

@Composable
fun PermissionRow(
    icon: ImageVector, title: String, hint: String, granted: Boolean, s: lk.sentinelmesh.app.Strings,
    inCard: Boolean = false, onAllow: () -> Unit,
) {
    Row(
        if (inCard) Modifier.fillMaxWidth().padding(vertical = 8.dp)
        else Modifier.fillMaxWidth().padding(vertical = 5.dp).clip(RoundedCornerShape(18.dp)).background(C.paper).padding(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(42.dp).clip(CircleShape).background(if (granted) Color(0xFFDFF1E6) else Color(0xFFE3EDF2)), contentAlignment = Alignment.Center) {
            Icon(if (granted) Icons.Filled.Check else icon, null, tint = if (granted) C.clear else C.river)
        }
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(hint, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
        }
        if (granted) Text(s.allowed, color = C.clear, style = MaterialTheme.typography.labelLarge, fontWeight = FontWeight.Bold)
        else TextButton(onClick = onAllow) { Text(s.allow, style = MaterialTheme.typography.labelLarge) }
    }
}
