package lk.sentinelmesh.app.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Notifications
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import lk.sentinelmesh.app.Lang
import lk.sentinelmesh.app.Strings
import lk.sentinelmesh.app.Zone
import lk.sentinelmesh.app.ZoneState

/** Native controls, shared across screens, using the web console's palette and typography. */
@Composable
fun LanguageSelector(current: Lang, onSelect: (Lang) -> Unit, modifier: Modifier = Modifier) {
    BoxWithConstraints(modifier.fillMaxWidth()) {
    if (maxWidth < 360.dp && LocalDensity.current.fontScale > 1.2f) {
        Column(Modifier.fillMaxWidth()) {
            Lang.entries.forEach { lang ->
                FilterChip(selected = current == lang, onClick = { onSelect(lang) }, label = { Text(lang.native) }, modifier = Modifier.fillMaxWidth().testTag("language-${lang.code}"))
            }
        }
    } else SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
        Lang.entries.forEachIndexed { index, lang ->
            SegmentedButton(
                selected = lang == current, onClick = { onSelect(lang) },
                shape = SegmentedButtonDefaults.itemShape(index, Lang.entries.size),
                modifier = Modifier.testTag("language-${lang.code}"),
                colors = SegmentedButtonDefaults.colors(activeContainerColor = C.ink, activeContentColor = Color.White),
                icon = {},
            ) { Text(lang.native, maxLines = 1) }
        }
    }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun StatusCard(zone: Zone?, state: ZoneState?, downloadedAt: Long, s: Strings, modifier: Modifier = Modifier) {
    val tier = state?.tier ?: "unknown"
    val blind = state?.blind == true
    val label = if (state == null) s.unknown else if (blind) s.blind else s.tier[tier] ?: tier
    Card(modifier.fillMaxWidth(), padding = 0.dp) {
        Box(Modifier.fillMaxWidth().height(5.dp).background(tierColor(tier, blind)))
        Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            FlowRow(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalArrangement = Arrangement.spacedBy(8.dp)) {
                SectionLabel(s.nowIn)
                TierPill(label, tier, blind)
            }
            Text(zone?.name ?: s.yourArea, style = MaterialTheme.typography.displaySmall)
            Text(listOfNotNull(zone?.district, zone?.let { s.hazardOf(it.hazard) }).joinToString(" · "), color = C.inkSoft)
            if (state == null) Text(s.statusUnavailable, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
            if (blind) Text(s.blindHint, style = MaterialTheme.typography.bodyMedium, color = C.blind)
            Spacer(Modifier.height(4.dp))
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TIER_ORDER.forEach { level ->
                    val selected = level == tier
                    Column(Modifier.weight(1f).semantics { contentDescription = "${s.tier[level]}${if (selected) ", $label" else ""}" }) {
                        Box(Modifier.fillMaxWidth().height(if (selected) 8.dp else 5.dp).clip(RoundedCornerShape(2.dp)).background(tierColor(level)))
                    }
                }
            }
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                Text(s.tier.getValue("normal"), style = MaterialTheme.typography.labelMedium, color = C.inkSoft)
                Text(s.tier.getValue("imminent"), style = MaterialTheme.typography.labelMedium, color = C.inkSoft)
            }
            if (downloadedAt > 0) {
                HorizontalDivider(color = C.rule, modifier = Modifier.padding(top = 6.dp))
                Text("${s.cachedStatus} · ${clock(downloadedAt)}", style = MaterialTheme.typography.labelMedium, color = C.inkSoft)
            }
        }
    }
}

@Composable
fun EmptyState(title: String, hint: String, modifier: Modifier = Modifier) {
    Card(modifier.fillMaxWidth()) {
        Icon(Icons.Filled.Notifications, null, tint = C.river, modifier = Modifier.size(30.dp))
        Spacer(Modifier.height(10.dp))
        Text(title, style = MaterialTheme.typography.titleLarge)
        Spacer(Modifier.height(4.dp))
        Text(hint, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
    }
}

@Composable
fun RefreshFeedback(failed: Boolean, s: Strings, onRetry: () -> Unit) {
    if (failed) Surface(color = C.paper, border = BorderStroke(1.dp, C.rule), shape = RoundedCornerShape(12.dp)) {
        Column(Modifier.fillMaxWidth().padding(14.dp)) {
            Text(s.refreshError, style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
            TextButton(onClick = onRetry, modifier = Modifier.testTag("retry-refresh")) { Text(s.refresh) }
        }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun RiskLegend(s: Strings) {
    FlowRow(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(8.dp)).background(C.paper.copy(alpha = 0.96f))
            .padding(12.dp),
        horizontalArrangement = Arrangement.spacedBy(14.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        TIER_ORDER.forEach { level ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Dot(tierColor(level), 10.dp)
                Spacer(Modifier.width(5.dp))
                Text(s.tier[level] ?: level, style = MaterialTheme.typography.labelMedium, maxLines = 1)
            }
        }
    }
}
