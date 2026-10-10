package lk.sentinelmesh.app.ui

import android.Manifest
import android.annotation.SuppressLint
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.location.LocationManager
import android.net.Uri
import android.os.Build
import android.os.CancellationSignal
import android.os.PowerManager
import android.provider.Settings
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.LocationOn
import androidx.compose.material.icons.filled.Search
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.platform.testTag
import androidx.core.content.ContextCompat
import androidx.core.location.LocationManagerCompat
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withTimeoutOrNull
import lk.sentinelmesh.app.Geo
import lk.sentinelmesh.app.LatLon
import lk.sentinelmesh.app.Strings
import lk.sentinelmesh.app.Zone
import lk.sentinelmesh.app.ZoneState
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.coroutines.resume

fun clock(ms: Long): String {
    val today = SimpleDateFormat("yyyyMMdd", Locale.US).format(Date())
    val day = SimpleDateFormat("yyyyMMdd", Locale.US).format(Date(ms))
    return SimpleDateFormat(if (day == today) "HH:mm" else "d MMM, HH:mm", Locale.US).format(Date(ms))
}

/** Where the phone is, using Android's own location service (no Google Play services needed). */
@SuppressLint("MissingPermission")
suspend fun currentLocation(ctx: Context): LatLon? {
    val lm = ctx.getSystemService(LocationManager::class.java)
    val providers = buildList {
        if (Build.VERSION.SDK_INT >= 31) add(LocationManager.FUSED_PROVIDER)
        add(LocationManager.NETWORK_PROVIDER); add(LocationManager.GPS_PROVIDER)
    }.filter { runCatching { lm.isProviderEnabled(it) }.getOrDefault(false) }
    for (p in providers) {
        val loc = withTimeoutOrNull(12_000) {
            suspendCancellableCoroutine { cont ->
                val cancel = CancellationSignal()
                cont.invokeOnCancellation { cancel.cancel() }
                LocationManagerCompat.getCurrentLocation(lm, p, cancel, ContextCompat.getMainExecutor(ctx)) { cont.resume(it) }
            }
        } ?: runCatching { lm.getLastKnownLocation(p) }.getOrNull()
        if (loc != null) return LatLon(loc.latitude, loc.longitude)
    }
    return null
}

fun hasLocationPermission(ctx: Context) =
    ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

fun notificationsAllowed(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).areNotificationsEnabled() &&
    (Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED)

fun openAppNotificationSettings(ctx: Context) {
    runCatching {
        ctx.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, ctx.packageName))
    }
}

fun fullScreenAllowed(ctx: Context) = Build.VERSION.SDK_INT < 34 ||
    ctx.getSystemService(NotificationManager::class.java).canUseFullScreenIntent()

fun backgroundAllowed(ctx: Context) = ctx.getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(ctx.packageName)

fun openFullScreenSettings(ctx: Context) {
    if (Build.VERSION.SDK_INT >= 34) runCatching {
        ctx.startActivity(Intent(Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT, Uri.parse("package:${ctx.packageName}")))
    }
}

@SuppressLint("BatteryLife")
fun openBatterySettings(ctx: Context) {
    runCatching {
        ctx.startActivity(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${ctx.packageName}")))
    }.onFailure { runCatching { ctx.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) } }
}

@Composable
fun TierPill(label: String, tier: String, blind: Boolean = false) {
    Text(
        label, color = onTier(tier, blind), style = MaterialTheme.typography.labelMedium,
        modifier = Modifier.clip(RoundedCornerShape(4.dp)).background(tierColor(tier, blind)).padding(horizontal = 10.dp, vertical = 5.dp),
    )
}

@Composable
fun PrimaryButton(text: String, onClick: () -> Unit, modifier: Modifier = Modifier, enabled: Boolean = true, color: Color = C.ink) {
    Button(
        onClick = onClick, enabled = enabled, modifier = modifier.heightIn(min = 54.dp),
        shape = RoundedCornerShape(10.dp), colors = ButtonDefaults.buttonColors(containerColor = color, contentColor = Color.White),
    ) { Text(text, style = MaterialTheme.typography.titleMedium) }
}

/** Search + "use my location" + every area grouped by district. */
@Composable
fun AreaPicker(
    s: Strings,
    zones: List<Zone>,
    states: Map<String, ZoneState>,
    selectedId: String?,
    onPick: (Zone) -> Unit,
    modifier: Modifier = Modifier,
) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var query by rememberSaveable { mutableStateOf("") }
    var locating by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf<String?>(null) }
    val locate = {
        scope.launch {
            locating = true; note = null
            val p = currentLocation(ctx)
            locating = false
            val z = p?.let { Geo.locate(zones, it) }
            if (z != null) onPick(z) else note = s.notCovered
        }
    }
    val permission = androidx.activity.compose.rememberLauncherForActivityResult(
        androidx.activity.result.contract.ActivityResultContracts.RequestMultiplePermissions(),
    ) { granted -> if (granted.values.any { it }) locate() else note = s.notCovered }

    val shown = zones.filter {
        query.isBlank() || it.name.contains(query, true) || it.district.contains(query, true) || it.id.contains(query, true)
    }
    LazyColumn(modifier, verticalArrangement = Arrangement.spacedBy(8.dp)) {
        item {
            Button(
                onClick = {
                    if (hasLocationPermission(ctx)) locate()
                    else permission.launch(arrayOf(Manifest.permission.ACCESS_COARSE_LOCATION, Manifest.permission.ACCESS_FINE_LOCATION))
                },
                enabled = !locating && zones.isNotEmpty(),
                modifier = Modifier.fillMaxWidth().heightIn(min = 56.dp), shape = RoundedCornerShape(10.dp),
                colors = ButtonDefaults.buttonColors(containerColor = C.river),
            ) {
                Icon(Icons.Filled.LocationOn, null)
                Spacer(Modifier.width(8.dp))
                Text(if (locating) s.locating else s.useMyLocation, style = MaterialTheme.typography.titleMedium)
            }
            note?.let { Text(it, color = C.warning, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.padding(top = 8.dp)) }
            Spacer(Modifier.height(10.dp))
            SectionLabel(s.pickArea)
            Spacer(Modifier.height(6.dp))
            OutlinedTextField(
                value = query, onValueChange = { query = it }, singleLine = true, modifier = Modifier.fillMaxWidth().testTag("area-search"),
                leadingIcon = { Icon(Icons.Filled.Search, null) }, placeholder = { Text(s.searchArea) },
                label = { Text(s.searchArea) },
                shape = RoundedCornerShape(10.dp),
                colors = OutlinedTextFieldDefaults.colors(focusedContainerColor = C.paper, unfocusedContainerColor = C.paper, unfocusedBorderColor = C.rule),
            )
        }
        if (shown.isEmpty() && zones.isNotEmpty()) item { Text(s.noAreas, color = C.inkSoft, modifier = Modifier.padding(12.dp)) }
        shown.groupBy { it.district }.forEach { (district, list) ->
            item(key = "d-$district") {
                Text(district, style = MaterialTheme.typography.titleMedium, color = C.inkSoft, modifier = Modifier.padding(top = 10.dp, start = 4.dp))
            }
            items(list, key = { it.id }) { z ->
                val st = states[z.id]
                val sel = z.id == selectedId
                Row(
                    Modifier.fillMaxWidth().testTag("area-${z.id}").clip(RoundedCornerShape(10.dp)).background(if (sel) Color(0xFFD6E6EE) else C.paper)
                        .border(if (sel) 2.dp else 1.dp, if (sel) C.river else C.rule, RoundedCornerShape(10.dp))
                        .clickable { onPick(z) }.padding(horizontal = 16.dp, vertical = 14.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(Modifier.size(12.dp).clip(CircleShape).background(tierColor(st?.tier, st?.blind == true)))
                    Spacer(Modifier.width(12.dp))
                    Column(Modifier.weight(1f)) {
                        Text(z.name, style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                        Text(s.hazardOf(z.hazard), style = MaterialTheme.typography.bodyMedium, color = C.inkSoft)
                    }
                    if (st != null && st.tier != "normal") TierPill(s.tier[st.tier] ?: st.tier, st.tier, st.blind)
                }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

fun Strings.hazardOf(h: String) = if (h == "landslide") landslide else flood
