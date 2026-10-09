package lk.sentinelmesh.app.ui

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import lk.sentinelmesh.app.AlertMsg
import lk.sentinelmesh.app.Lang
import lk.sentinelmesh.app.Link
import lk.sentinelmesh.app.Sentinel
import lk.sentinelmesh.app.Strings
import lk.sentinelmesh.app.Zone
import lk.sentinelmesh.app.strings

@Composable
fun MainScreen(resumeTick: Int) {
    val lang by Sentinel.lang.collectAsState()
    val s = strings(lang)
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val tabs = listOf<Pair<String, ImageVector>>(s.home to Icons.Filled.Home, s.map to Icons.Filled.Place, s.alerts to Icons.Filled.Notifications, s.settings to Icons.Filled.Settings)
    Scaffold(
        containerColor = C.mist,
        bottomBar = {
            NavigationBar(containerColor = C.paper, tonalElevation = 0.dp) {
                tabs.forEachIndexed { i, (label, icon) ->
                    NavigationBarItem(
                        selected = tab == i, onClick = { tab = i }, icon = { Icon(icon, null) },
                        label = { Text(label, maxLines = 1) },
                        colors = NavigationBarItemDefaults.colors(indicatorColor = Color(0xFFD6E6EE), selectedIconColor = C.ink, selectedTextColor = C.ink),
                    )
                }
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            when (tab) {
                0 -> HomeTab(s, lang) { tab = 1 }
                1 -> MapTab(s)
                2 -> AlertsTab(s, lang)
                else -> SettingsTab(s, lang, resumeTick)
            }
        }
    }
}

@Composable
fun LinkPill(s: Strings) {
    val link by Sentinel.link.collectAsState()
    val (color, label) = when (link) {
        Link.LISTENING -> C.live to s.listening
        Link.CONNECTING -> C.watch to s.connecting
        Link.OFFLINE -> C.warning to s.offline
    }
    val pulse = rememberInfiniteTransition(label = "pulse").animateFloat(0.35f, 1f, infiniteRepeatable(tween(1100), RepeatMode.Reverse), label = "a")
    Row(
        Modifier.clip(RoundedCornerShape(50)).background(Color(0x1A12324A)).padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(9.dp).alpha(if (link == Link.LISTENING) pulse.value else 1f).clip(CircleShape).background(color))
        Spacer(Modifier.width(7.dp))
        Text(label, style = MaterialTheme.typography.labelLarge, color = C.ink, maxLines = 1)
    }
}

@Composable
private fun HomeTab(s: Strings, lang: Lang, openMap: () -> Unit) {
    val zones by Sentinel.zones.collectAsState()
    val states by Sentinel.states.collectAsState()
    val statesAt by Sentinel.statesAt.collectAsState()
    val myId by Sentinel.myZoneId.collectAsState()
    val history by Sentinel.history.collectAsState()
    val zone = zones.firstOrNull { it.id == myId }
    val st = states[myId]
    val tier = st?.tier ?: "normal"
    val blind = st?.blind == true
    val latest = history.firstOrNull { it.zoneId == myId || it.channel == "test" }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).statusBarsPadding().padding(horizontal = 18.dp)) {
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("SentinelMesh", style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            LinkPill(s)
        }
        Spacer(Modifier.height(14.dp))

        // Hero: my area's status, in its risk colour.
        val base = tierColor(tier, blind)
        val fg = onTier(tier, blind)
        Box(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(28.dp))
                .background(Brush.linearGradient(listOf(base, base.copy(alpha = 0.82f).compositeOver(C.ink))))
                .padding(22.dp),
        ) {
            Column {
                Text(s.nowIn.uppercase(), style = MaterialTheme.typography.labelMedium, color = fg.copy(alpha = 0.8f))
                Spacer(Modifier.height(6.dp))
                Text(if (blind) s.blind else (s.tier[tier] ?: tier), style = MaterialTheme.typography.displaySmall, color = fg)
                Spacer(Modifier.height(4.dp))
                Text(zone?.name ?: "—", style = MaterialTheme.typography.titleLarge, color = fg)
                Text(listOfNotNull(zone?.district, zone?.let { s.hazardOf(it.hazard) }).joinToString(" · "), style = MaterialTheme.typography.bodyLarge, color = fg.copy(alpha = 0.85f))
                if (blind) Text(s.blindHint, style = MaterialTheme.typography.bodyMedium, color = fg, modifier = Modifier.padding(top = 8.dp))
                Spacer(Modifier.height(16.dp))
                // Risk ladder: where this area sits.
                Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    TIER_ORDER.forEach { t ->
                        val on = t == tier
                        Box(Modifier.weight(1f).height(if (on) 10.dp else 6.dp).clip(RoundedCornerShape(5.dp))
                            .background(if (on) Color.White else tierColor(t).copy(alpha = 0.55f)))
                    }
                }
                if (statesAt > 0) Text("${s.updated} ${clock(statesAt)}", style = MaterialTheme.typography.labelMedium, color = fg.copy(alpha = 0.75f), modifier = Modifier.padding(top = 10.dp))
            }
        }

        Spacer(Modifier.height(14.dp))
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.whatToDo)
            Spacer(Modifier.height(10.dp))
            (s.guidance[tier] ?: s.guidance.getValue("normal")).forEachIndexed { i, line ->
                Row(Modifier.padding(vertical = 5.dp), verticalAlignment = Alignment.Top) {
                    Box(Modifier.size(26.dp).clip(CircleShape).background(tierColor(tier).copy(alpha = 0.22f)), contentAlignment = Alignment.Center) {
                        Text("${i + 1}", style = MaterialTheme.typography.labelLarge, color = C.ink)
                    }
                    Spacer(Modifier.width(12.dp))
                    Text(line, style = MaterialTheme.typography.bodyLarge, modifier = Modifier.padding(top = 2.dp))
                }
            }
        }

        Spacer(Modifier.height(14.dp))
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.latestAlert)
            Spacer(Modifier.height(8.dp))
            if (latest == null) {
                Text(s.noAlerts, style = MaterialTheme.typography.titleMedium)
                Text(s.noAlertsHint, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
            } else AlertRow(latest, s, lang)
        }

        Spacer(Modifier.height(14.dp))
        // District overview with a small live map.
        val district = zones.filter { it.district == zone?.district }
        val atRisk = district.count { (states[it.id]?.tier ?: "normal") != "normal" }
        Card(Modifier.fillMaxWidth().clickable { openMap() }, padding = 0.dp) {
            Column(Modifier.padding(start = 18.dp, end = 18.dp, top = 16.dp, bottom = 10.dp)) {
                SectionLabel("${s.district} · ${zone?.district ?: ""}")
                Text(if (atRisk == 0) s.allAreasNormal else "$atRisk ${s.areasAtRisk}", style = MaterialTheme.typography.titleLarge)
            }
            if (district.isNotEmpty()) {
                ZoneMap(
                    zones, states, myId, null, onSelect = { openMap() }, focus = district, interactive = false,
                    modifier = Modifier.fillMaxWidth().height(190.dp).clip(RoundedCornerShape(bottomStart = 22.dp, bottomEnd = 22.dp)),
                )
            }
        }
        Spacer(Modifier.height(20.dp))
    }
}

private fun Color.compositeOver(bg: Color): Color {
    val a = alpha
    return Color(red * a + bg.red * (1 - a), green * a + bg.green * (1 - a), blue * a + bg.blue * (1 - a), 1f)
}

@Composable
fun AlertRow(m: AlertMsg, s: Strings, lang: Lang) {
    val zone = Sentinel.zone(m.zoneId)
    Row(Modifier.fillMaxWidth()) {
        Box(Modifier.width(6.dp).height(78.dp).clip(RoundedCornerShape(3.dp)).background(tierColor(m.tier)))
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                TierPill(s.headline[m.tier] ?: m.tier, m.tier)
                if (m.status != "Actual") {
                    Spacer(Modifier.width(6.dp))
                    Text(s.exercise, style = MaterialTheme.typography.labelMedium, color = C.inkSoft)
                }
                Spacer(Modifier.weight(1f))
                Text(clock(m.receivedAt), style = MaterialTheme.typography.labelMedium, color = C.inkSoft)
            }
            Spacer(Modifier.height(6.dp))
            Text(m.text(lang), style = MaterialTheme.typography.bodyLarge)
            val via = when (m.channel) { "push" -> s.viaPush; "catchup" -> s.viaCatchUp; else -> s.viaTest }
            Text(listOfNotNull(zone?.name, "${s.via} $via").joinToString(" · "), style = MaterialTheme.typography.labelMedium, color = C.inkSoft, modifier = Modifier.padding(top = 4.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MapTab(s: Strings) {
    val zones by Sentinel.zones.collectAsState()
    val states by Sentinel.states.collectAsState()
    val myId by Sentinel.myZoneId.collectAsState()
    var selected by remember { mutableStateOf<Zone?>(null) }
    Box(Modifier.fillMaxSize()) {
        ZoneMap(zones, states, myId, selected?.id, onSelect = { selected = it }, modifier = Modifier.fillMaxSize())
        Column(Modifier.statusBarsPadding().padding(12.dp)) {
            Row(
                Modifier.clip(RoundedCornerShape(18.dp)).background(C.paper.copy(alpha = 0.94f)).padding(horizontal = 12.dp, vertical = 10.dp),
                horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically,
            ) {
                TIER_ORDER.forEach { t ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Dot(tierColor(t), 10.dp)
                        Spacer(Modifier.width(4.dp))
                        Text(s.tier[t] ?: t, style = MaterialTheme.typography.labelMedium, maxLines = 1)
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Text(
                s.tapAnArea, style = MaterialTheme.typography.labelMedium, color = C.inkSoft,
                modifier = Modifier.clip(RoundedCornerShape(12.dp)).background(C.paper.copy(alpha = 0.9f)).padding(horizontal = 10.dp, vertical = 6.dp),
            )
        }
    }
    selected?.let { z ->
        val st = states[z.id]
        val tier = st?.tier ?: "normal"
        ModalBottomSheet(onDismissRequest = { selected = null }, sheetState = rememberModalBottomSheetState(), containerColor = C.paper) {
            Column(Modifier.padding(horizontal = 22.dp).padding(bottom = 28.dp)) {
                TierPill(if (st?.blind == true) s.blind else (s.tier[tier] ?: tier), tier, st?.blind == true)
                Spacer(Modifier.height(8.dp))
                Text(z.name, style = MaterialTheme.typography.headlineSmall)
                Text("${z.district} · ${s.hazardOf(z.hazard)}", style = MaterialTheme.typography.bodyLarge, color = C.inkSoft)
                Spacer(Modifier.height(12.dp))
                (s.guidance[tier] ?: emptyList()).take(2).forEach { Text("• $it", style = MaterialTheme.typography.bodyLarge) }
                if (z.id != myId) {
                    Spacer(Modifier.height(16.dp))
                    PrimaryButton(s.setAsMyArea, { Sentinel.setZone(z.id); selected = null }, Modifier.fillMaxWidth(), color = C.river)
                }
            }
        }
    }
}

@Composable
private fun AlertsTab(s: Strings, lang: Lang) {
    val history by Sentinel.history.collectAsState()
    LazyColumn(Modifier.fillMaxSize().statusBarsPadding(), contentPadding = PaddingValues(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(s.alerts, style = MaterialTheme.typography.displaySmall, modifier = Modifier.weight(1f))
                LinkPill(s)
            }
        }
        if (history.isEmpty()) item {
            Card(Modifier.fillMaxWidth()) {
                Text(s.noAlerts, style = MaterialTheme.typography.titleMedium)
                Text(s.noAlertsHint, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
            }
        }
        items(history, key = { it.alertId }) { m -> Card(Modifier.fillMaxWidth()) { AlertRow(m, s, lang) } }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SettingsTab(s: Strings, lang: Lang, resumeTick: Int) {
    val ctx = LocalContext.current
    val zones by Sentinel.zones.collectAsState()
    val states by Sentinel.states.collectAsState()
    val myId by Sentinel.myZoneId.collectAsState()
    var picking by remember { mutableStateOf(false) }
    @Suppress("UNUSED_VARIABLE") val refresh = resumeTick
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).statusBarsPadding().padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(s.settings, style = MaterialTheme.typography.displaySmall)
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.language)
            Spacer(Modifier.height(10.dp))
            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(C.mist).padding(4.dp)) {
                Lang.entries.forEach { l ->
                    val sel = l == lang
                    Box(
                        Modifier.weight(1f).clip(RoundedCornerShape(11.dp)).background(if (sel) C.ink else Color.Transparent)
                            .clickable { Sentinel.setLang(l) }.padding(vertical = 12.dp),
                        contentAlignment = Alignment.Center,
                    ) { Text(l.native, color = if (sel) Color.White else C.ink, style = MaterialTheme.typography.titleMedium) }
                }
            }
        }
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.area)
            Row(verticalAlignment = Alignment.CenterVertically) {
                val z = Sentinel.zone(myId)
                Column(Modifier.weight(1f)) {
                    Text(z?.name ?: "—", style = MaterialTheme.typography.titleLarge)
                    Text(z?.district ?: "", style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
                }
                TextButton(onClick = { picking = true }) { Text(s.change, style = MaterialTheme.typography.labelLarge) }
            }
        }
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.stayProtected)
            PermissionRow(Icons.Filled.Notifications, s.permNotifications, s.permNotificationsHint, notificationsAllowed(ctx), s, inCard = true) { openAppNotificationSettings(ctx) }
            PermissionRow(Icons.Filled.Warning, s.permFullScreen, s.permFullScreenHint, fullScreenAllowed(ctx), s, inCard = true) { openFullScreenSettings(ctx) }
            PermissionRow(Icons.Filled.Refresh, s.permBackground, s.permBackgroundHint, backgroundAllowed(ctx), s, inCard = true) { openBatterySettings(ctx) }
        }
        Card(Modifier.fillMaxWidth()) {
            Text(s.testAlarm, style = MaterialTheme.typography.titleMedium)
            Text(s.testAlarmHint, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
            Spacer(Modifier.height(10.dp))
            PrimaryButton(s.testAlarm, { Sentinel.testAlarm() }, Modifier.fillMaxWidth(), color = C.imminent)
        }
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.privacy)
            Spacer(Modifier.height(4.dp))
            Text(s.privacyText, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
            Spacer(Modifier.height(10.dp))
            Text("SentinelMesh · TheByteForce · AWS IoT Core + Amazon Cognito + CloudFront", style = MaterialTheme.typography.labelMedium, color = C.inkSoft, fontWeight = FontWeight.Normal)
        }
    }
    if (picking) {
        ModalBottomSheet(onDismissRequest = { picking = false }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = C.mist) {
            Column(Modifier.padding(horizontal = 18.dp)) {
                Text(s.yourArea, style = MaterialTheme.typography.headlineSmall)
                Spacer(Modifier.height(10.dp))
                AreaPicker(s, zones, states, myId, onPick = { Sentinel.setZone(it.id); picking = false }, modifier = Modifier.fillMaxWidth().height(560.dp))
            }
        }
    }
}

private fun openAppNotificationSettings(ctx: android.content.Context) {
    runCatching {
        ctx.startActivity(android.content.Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, ctx.packageName))
    }
}
