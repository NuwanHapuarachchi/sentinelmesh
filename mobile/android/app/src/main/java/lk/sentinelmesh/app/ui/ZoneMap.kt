package lk.sentinelmesh.app.ui

import android.graphics.BitmapFactory
import android.util.LruCache
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.gestures.detectTransformGestures
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntSize
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.SupervisorJob
import lk.sentinelmesh.app.LatLon
import lk.sentinelmesh.app.Net
import lk.sentinelmesh.app.Zone
import lk.sentinelmesh.app.ZoneState
import okhttp3.Request
import kotlin.math.PI
import kotlin.math.atan
import kotlin.math.floor
import kotlin.math.ln
import kotlin.math.log2
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.tan

// Web Mercator in "world units" at zoom 0 (0..256 across the world).
private fun wx(lon: Double) = (lon + 180.0) / 360.0 * 256.0
private fun wy(lat: Double): Double {
    val r = Math.toRadians(lat)
    return (1.0 - ln(tan(r) + 1.0 / kotlin.math.cos(r)) / PI) / 2.0 * 256.0
}
private fun lonOf(x: Double) = x / 256.0 * 360.0 - 180.0
private fun latOf(y: Double) = Math.toDegrees(atan(kotlin.math.sinh(PI * (1 - 2 * y / 256.0))))

/** Light grey base map tiles (Esri), drawn under the zones. Kept in memory only. */
private object Tiles {
    private val cache = LruCache<String, ImageBitmap>(160)
    private val loading = HashSet<String>()
    private val io = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    var version by mutableIntStateOf(0)

    fun get(z: Int, x: Int, y: Int): ImageBitmap? {
        val key = "$z/$y/$x"
        cache.get(key)?.let { return it }
        synchronized(loading) { if (!loading.add(key)) return null }
        io.launch {
            runCatching {
                val url = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/$key"
                Net.http.newCall(Request.Builder().url(url).build()).execute().use { r ->
                    if (r.isSuccessful) {
                        val bytes = r.body.bytes()
                        BitmapFactory.decodeByteArray(bytes, 0, bytes.size)?.let { cache.put(key, it.asImageBitmap()) }
                    }
                }
            }
            synchronized(loading) { loading.remove(key) }
            version++
        }
        return null
    }
}

@Composable
fun ZoneMap(
    zones: List<Zone>,
    states: Map<String, ZoneState>,
    myZoneId: String?,
    selectedId: String?,
    onSelect: (Zone?) -> Unit,
    modifier: Modifier = Modifier,
    focus: List<Zone> = zones,
    interactive: Boolean = true,
) {
    val density = LocalDensity.current.density
    var size by remember { mutableStateOf(IntSize.Zero) }
    var cx by remember { mutableFloatStateOf(0f) }
    var cy by remember { mutableFloatStateOf(0f) }
    var scale by remember { mutableFloatStateOf(0f) } // screen px per world unit
    val focusKey = focus.joinToString(",") { it.id }

    // Fit the focus zones whenever they (or the view size) change.
    LaunchedEffect(focusKey, size) {
        if (size.width == 0 || focus.isEmpty()) return@LaunchedEffect
        val pts = focus.flatMap { it.rings.first() }
        val minX = pts.minOf { wx(it.lon) }; val maxX = pts.maxOf { wx(it.lon) }
        val minY = pts.minOf { wy(it.lat) }; val maxY = pts.maxOf { wy(it.lat) }
        cx = ((minX + maxX) / 2).toFloat(); cy = ((minY + maxY) / 2).toFloat()
        val pad = 0.78f
        scale = min(size.width * pad / max(maxX - minX, 1e-6).toFloat(), size.height * pad / max(maxY - minY, 1e-6).toFloat())
            .coerceAtMost(2.0.pow(15.5).toFloat())
    }

    fun toScreen(p: LatLon) = Offset(
        ((wx(p.lon) - cx) * scale + size.width / 2).toFloat(),
        ((wy(p.lat) - cy) * scale + size.height / 2).toFloat(),
    )

    fun fromScreen(o: Offset) = LatLon(
        latOf((o.y - size.height / 2) / scale + cy.toDouble()),
        lonOf((o.x - size.width / 2) / scale + cx.toDouble()),
    )

    var gestures = Modifier.pointerInput(zones) {
        detectTapGestures { tap ->
            if (scale > 0) {
                val p = fromScreen(tap)
                onSelect(zones.firstOrNull { it.contains(p) })
            }
        }
    }
    if (interactive) gestures = gestures.pointerInput(Unit) {
        detectTransformGestures { centroid, pan, zoom, _ ->
            if (scale <= 0) return@detectTransformGestures
            // Zoom around the fingers, then pan.
            val before = Offset((centroid.x - size.width / 2) / scale + cx, (centroid.y - size.height / 2) / scale + cy)
            scale = (scale * zoom).coerceIn(2f, 2.0.pow(17.0).toFloat())
            cx = before.x - (centroid.x - size.width / 2) / scale
            cy = before.y - (centroid.y - size.height / 2) / scale
            cx -= pan.x / scale
            cy -= pan.y / scale
        }
    }

    Canvas(modifier.then(gestures).onSizeChanged { size = it }) {
        if (scale <= 0) return@Canvas
        @Suppress("UNUSED_VARIABLE") val v = Tiles.version // redraw when a tile arrives
        drawRect(Color(0xFFE4E8EA))
        // Base map: tile zoom chosen so tiles are drawn at about their native sharpness.
        val tz = floor(log2(scale / (density / 1.6))).toInt().coerceIn(3, 17)
        val n = 1 shl tz
        val tileWorld = 256.0 / n
        val left = cx - size.width / 2 / scale; val top = cy - size.height / 2 / scale
        val x0 = floor(left / tileWorld).toInt(); val y0 = floor(top / tileWorld).toInt()
        val x1 = floor((left + size.width / scale) / tileWorld).toInt(); val y1 = floor((top + size.height / scale) / tileWorld).toInt()
        val px = (tileWorld * scale).toFloat()
        for (tx in x0..x1) for (ty in y0..y1) {
            if (ty !in 0 until n) continue
            val img = Tiles.get(tz, ((tx % n) + n) % n, ty) ?: continue
            val sx = ((tx * tileWorld - cx) * scale + size.width / 2).toFloat()
            val sy = ((ty * tileWorld - cy) * scale + size.height / 2).toFloat()
            drawImage(img, dstOffset = IntOffset(sx.toInt(), sy.toInt()), dstSize = IntSize(px.toInt() + 1, px.toInt() + 1))
        }
        // Zones, coloured by risk.
        val stroke = max(1f, 1.2f * density)
        for (z in zones) {
            val st = states[z.id]
            val tier = st?.tier ?: "normal"
            val path = Path()
            for (ring in z.rings) {
                ring.forEachIndexed { i, p -> val o = toScreen(p); if (i == 0) path.moveTo(o.x, o.y) else path.lineTo(o.x, o.y) }
                path.close()
            }
            val color = tierColor(tier, st?.blind == true)
            drawPath(path, color.copy(alpha = if (tier == "normal" && st?.blind != true) 0.45f else 0.78f))
            drawPath(path, Color.White.copy(alpha = 0.85f), style = Stroke(stroke, join = StrokeJoin.Round))
            if (z.id == myZoneId) drawPath(path, C.river, style = Stroke(stroke * 3.2f, join = StrokeJoin.Round))
            if (z.id == selectedId) drawPath(path, C.ink, style = Stroke(stroke * 2.4f, join = StrokeJoin.Round))
        }
        // "You are here" pin on my zone.
        zones.firstOrNull { it.id == myZoneId }?.let { z ->
            val o = toScreen(z.center)
            drawCircle(C.river.copy(alpha = 0.22f), radius = 16f * density, center = o)
            drawCircle(Color.White, radius = 7f * density, center = o)
            drawCircle(C.river, radius = 5f * density, center = o)
        }
    }
}
