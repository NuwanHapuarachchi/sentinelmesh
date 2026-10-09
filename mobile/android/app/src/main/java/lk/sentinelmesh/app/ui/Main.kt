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
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.IconButton
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.heightIn
import androidx.compose.ui.platform.testTag
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
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
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

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(resumeTick: Int) {
    val lang by Sentinel.lang.collectAsState()
    val s = strings(lang)
    var tab by rememberSaveable { mutableIntStateOf(0) }
    val screenState = rememberSaveableStateHolder()
    val tabs = listOf<Pair<String, ImageVector>>(s.home to Icons.Filled.Home, s.map to Icons.Filled.Place, s.alerts to Icons.Filled.Notifications, s.settings to Icons.Filled.Settings)
    val refreshing by Sentinel.refreshing.collectAsState()
    val refreshFailed by Sentinel.refreshFailed.collectAsState()
    Scaffold(
        containerColor = C.mist,
        topBar = {
            TopAppBar(
                title = { Column {
                    Text("SentinelMesh", style = MaterialTheme.typography.headlineSmall)
                    Text(tabs[tab].first, style = MaterialTheme.typography.labelMedium, color = Color(0xFFCFDBE0))
                } },
                colors = TopAppBarDefaults.topAppBarColors(containerColor = C.ink, titleContentColor = Color.White, actionIconContentColor = Color.White),
                actions = {
                    if (refreshing) CircularProgressIndicator(Modifier.padding(16.dp).size(22.dp), color = Color.White, strokeWidth = 2.dp)
                    else IconButton(onClick = { Sentinel.refreshNow() }, modifier = Modifier.testTag("refresh-status")) {
                        Icon(Icons.Filled.Refresh, s.refresh)
                    }
                },
            )
        },
        bottomBar = {
            NavigationBar(containerColor = C.paper, tonalElevation = 0.dp) {
                tabs.forEachIndexed { i, (label, icon) ->
                    NavigationBarItem(
                        modifier = Modifier.testTag("navigation-$i"),
                        selected = tab == i, onClick = { tab = i }, icon = { Icon(icon, null) },
                        label = { Text(label, maxLines = 1) },
                        colors = NavigationBarItemDefaults.colors(indicatorColor = Color(0xFFD6E6EE), selectedIconColor = C.ink, selectedTextColor = C.ink),
                    )
                }
            }
        },
    ) { pad ->
        Box(Modifier.padding(pad).fillMaxSize()) {
            screenState.SaveableStateProvider(tab) {
            when (tab) {
                0 -> PullToRefreshBox(isRefreshing = refreshing, onRefresh = { Sentinel.refreshNow() }) {
                    HomeTab(s, lang, refreshFailed) { tab = 1 }
                }
                1 -> MapTab(s)
                2 -> AlertsTab(s, lang)
                else -> SettingsTab(s, lang, resumeTick)
            }
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
private fun HomeTab(s: Strings, lang: Lang, refreshFailed: Boolean, openMap: () -> Unit) {
    val zones by Sentinel.zones.collectAsState()
    val states by Sentinel.states.collectAsState()
    val statesAt by Sentinel.statesAt.collectAsState()
    val myId by Sentinel.myZoneId.collectAsState()
    val history by Sentinel.history.collectAsState()
    val zone = zones.firstOrNull { it.id == myId }
    val st = states[myId]
    val tier = st?.tier ?: "unknown"
    val latest = history.firstOrNull { it.zoneId == myId || it.channel == "test" }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 18.dp)) {
        Spacer(Modifier.height(10.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(s.yourArea, style = MaterialTheme.typography.titleLarge, modifier = Modifier.weight(1f))
            LinkPill(s)
        }
        Spacer(Modifier.height(14.dp))

        RefreshFeedback(refreshFailed, s) { Sentinel.refreshNow() }
        if (refreshFailed) Spacer(Modifier.height(12.dp))
        StatusCard(zone, st, statesAt, s)

        Spacer(Modifier.height(14.dp))
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.whatToDo)
            Spacer(Modifier.height(10.dp))
            (s.guidance[tier] ?: listOf(s.statusUnavailable)).forEachIndexed { i, line ->
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
                Icon(Icons.Filled.Notifications, null, tint = C.river)
                Spacer(Modifier.height(8.dp))
                Text(s.noAlerts, style = MaterialTheme.typography.titleLarge)
                Text(s.noAlertsHint, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
            } else AlertRow(latest, s, lang)
        }

        Spacer(Modifier.height(14.dp))
        // District overview with a small live map.
        val district = zones.filter { it.district == zone?.district }
        val atRisk = district.count { states[it.id]?.tier?.let { it != "normal" && it != "clear" } == true }
        Card(Modifier.fillMaxWidth().clickable { openMap() }, padding = 0.dp) {
            Column(Modifier.padding(start = 18.dp, end = 18.dp, top = 16.dp, bottom = 10.dp)) {
                SectionLabel("${s.district} · ${zone?.district ?: ""}")
                Text(if (district.any { states[it.id] == null }) s.statusUnavailable else if (atRisk == 0) s.allAreasNormal else "$atRisk ${s.areasAtRisk}", style = MaterialTheme.typography.titleLarge)
            }
            if (district.isNotEmpty()) {
                ZoneMap(
                    zones, states, myId, null, onSelect = { openMap() }, focus = district, interactive = false,
                    modifier = Modifier.fillMaxWidth().height(190.dp).clip(RoundedCornerShape(bottomStart = 12.dp, bottomEnd = 12.dp)),
                )
            }
        }
        Spacer(Modifier.height(20.dp))
    }
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
            }
            Spacer(Modifier.height(6.dp))
            Text(m.text(lang), style = MaterialTheme.typography.bodyLarge)
            val via = when (m.channel) { "push" -> s.viaPush; "catchup" -> s.viaCatchUp; else -> s.viaTest }
            Text(listOfNotNull(clock(m.receivedAt), zone?.name, "${s.via} $via").joinToString(" · "), style = MaterialTheme.typography.labelMedium, color = C.inkSoft, modifier = Modifier.padding(top = 4.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MapTab(s: Strings) {
    val zones by Sentinel.zones.collectAsState()
    val states by Sentinel.states.collectAsState()
    val myId by Sentinel.myZoneId.collectAsState()
    var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
    var searchOpen by rememberSaveable { mutableStateOf(false) }
    val selected = zones.firstOrNull { it.id == selectedId }
    Box(Modifier.fillMaxSize()) {
        if (zones.isEmpty()) Column(Modifier.padding(18.dp)) {
            EmptyState(s.yourArea, s.statusUnavailable)
            TextButton(onClick = { Sentinel.refreshNow() }) { Text(s.refresh) }
        } else ZoneMap(zones, states, myId, selected?.id, onSelect = { selectedId = it?.id }, modifier = Modifier.fillMaxSize())
        if (zones.isNotEmpty()) Column(Modifier.padding(12.dp)) {
            RiskLegend(s)
            Spacer(Modifier.height(8.dp))
            Text(
                s.tapAnArea, style = MaterialTheme.typography.labelMedium, color = C.inkSoft,
                modifier = Modifier.clip(RoundedCornerShape(12.dp)).background(C.paper.copy(alpha = 0.9f)).padding(horizontal = 10.dp, vertical = 6.dp),
            )
            TextButton(onClick = { searchOpen = true }, modifier = Modifier.testTag("map-search")) {
                Text(s.searchArea)
            }
        }
    }
    if (searchOpen) {
        ModalBottomSheet(onDismissRequest = { searchOpen = false }, containerColor = C.mist) {
            Column(Modifier.padding(horizontal = 18.dp)) {
                Text(s.yourArea, style = MaterialTheme.typography.headlineSmall)
                AreaPicker(s, zones, states, selectedId, onPick = { selectedId = it.id; searchOpen = false }, modifier = Modifier.fillMaxWidth().heightIn(max = 520.dp))
            }
        }
    }
    selected?.let { z ->
        val st = states[z.id]
        val tier = st?.tier ?: "unknown"
        ModalBottomSheet(onDismissRequest = { selectedId = null }, sheetState = rememberModalBottomSheetState(), containerColor = C.paper) {
            Column(Modifier.padding(horizontal = 22.dp).padding(bottom = 28.dp)) {
                TierPill(if (st?.blind == true) s.blind else (s.tier[tier] ?: s.unknown), tier, st?.blind == true)
                Spacer(Modifier.height(8.dp))
                Text(z.name, style = MaterialTheme.typography.headlineSmall)
                Text("${z.district} · ${s.hazardOf(z.hazard)}", style = MaterialTheme.typography.bodyLarge, color = C.inkSoft)
                Spacer(Modifier.height(12.dp))
                (s.guidance[tier] ?: listOf(s.statusUnavailable)).take(2).forEach { Text("• $it", style = MaterialTheme.typography.bodyLarge) }
                if (z.id != myId) {
                    Spacer(Modifier.height(16.dp))
                    PrimaryButton(s.setAsMyArea, { Sentinel.setZone(z.id); selectedId = null }, Modifier.fillMaxWidth(), color = C.river)
                }
            }
        }
    }
}

@Composable
private fun AlertsTab(s: Strings, lang: Lang) {
    val history by Sentinel.history.collectAsState()
    val area by Sentinel.myZoneId.collectAsState()
    AlertHistory(history, area, s, lang)
}

@Composable
fun AlertHistory(history: List<AlertMsg>, area: String?, s: Strings, lang: Lang) {
    var urgentOnly by rememberSaveable { mutableStateOf(false) }
    var areaOnly by rememberSaveable { mutableStateOf(false) }
    val filtered = history.filter { (!urgentOnly || it.urgent) && (!areaOnly || it.zoneId == area) }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item {
            Text(s.alerts, style = MaterialTheme.typography.headlineSmall)
            Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = !urgentOnly, onClick = { urgentOnly = false }, label = { Text(s.allAlerts) }, modifier = Modifier.testTag("filter-all"))
                FilterChip(selected = urgentOnly, onClick = { urgentOnly = true }, label = { Text(s.urgentAlerts) }, modifier = Modifier.testTag("filter-urgent"))
                FilterChip(selected = areaOnly, onClick = { areaOnly = !areaOnly }, label = { Text(s.yourArea) }, modifier = Modifier.testTag("filter-area"))
            }
        }
        if (filtered.isEmpty()) item {
            EmptyState(if (history.isEmpty()) s.noAlerts else s.noMatchingAlerts, s.noAlertsHint)
        }
        items(filtered, key = { it.alertId }) { m -> Card(Modifier.fillMaxWidth().testTag("alert-${m.alertId}")) { AlertRow(m, s, lang) } }
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
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(18.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(s.settings, style = MaterialTheme.typography.displaySmall)
        Card(Modifier.fillMaxWidth()) {
            SectionLabel(s.language)
            Spacer(Modifier.height(10.dp))
            LanguageSelector(lang, Sentinel::setLang)
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
            Text("SentinelMesh · TheByteForce · Resident early warnings", style = MaterialTheme.typography.labelMedium, color = C.inkSoft, fontWeight = FontWeight.Normal)
        }
    }
    if (picking) {
        ModalBottomSheet(onDismissRequest = { picking = false }, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = C.mist) {
            Column(Modifier.padding(horizontal = 18.dp)) {
                Text(s.yourArea, style = MaterialTheme.typography.headlineSmall)
                Spacer(Modifier.height(10.dp))
                AreaPicker(s, zones, states, myId, onPick = { Sentinel.setZone(it.id); picking = false }, modifier = Modifier.fillMaxWidth().heightIn(max = 520.dp))
            }
        }
    }
}

private fun openAppNotificationSettings(ctx: android.content.Context) {
    runCatching {
        ctx.startActivity(android.content.Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, ctx.packageName))
    }
}
