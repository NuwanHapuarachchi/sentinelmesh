package lk.sentinelmesh.app

import org.json.JSONArray
import org.json.JSONObject
import kotlin.math.cos
import kotlin.math.sqrt

data class LatLon(val lat: Double, val lon: Double)

data class Zone(
    val id: String,
    val name: String,
    val district: String,
    val hazard: String,
    val rings: List<List<LatLon>>,
) {
    val center: LatLon by lazy {
        val pts = rings.first()
        LatLon(pts.map { it.lat }.average(), pts.map { it.lon }.average())
    }

    fun contains(p: LatLon): Boolean {
        // Each ring is an outer polygon, including separate parts of a MultiPolygon zone.
        return rings.any { containsRing(it, p) }
    }

    private fun containsRing(ring: List<LatLon>, p: LatLon): Boolean {
        var inside = false
        var j = ring.size - 1
        for (i in ring.indices) {
            val a = ring[i]; val b = ring[j]
            if ((a.lat > p.lat) != (b.lat > p.lat) &&
                p.lon < (b.lon - a.lon) * (p.lat - a.lat) / (b.lat - a.lat) + a.lon
            ) inside = !inside
            j = i
        }
        return inside
    }
}

data class ZoneState(val tier: String, val blind: Boolean, val updatedAt: Long?)

/** One alert as the phone received it. Texts come in all three languages. */
data class AlertMsg(
    val alertId: String,
    val zoneId: String,
    val tier: String,
    val hazard: String,
    val status: String,
    val texts: Map<String, String>,
    val receivedAt: Long,
    val channel: String, // push | catchup | test
) {
    fun text(lang: Lang) = texts[lang.code] ?: texts["en"] ?: ""
    val urgent get() = tier == "imminent" || tier == "warning"

    fun toJson(): JSONObject = JSONObject()
        .put("alert_id", alertId).put("zone_id", zoneId).put("tier", tier).put("hazard", hazard)
        .put("status", status).put("texts", JSONObject(texts)).put("received_at", receivedAt).put("channel", channel)

    companion object {
        /** From a broadcast message (sm/broadcast/{zone} or cb/{zone}.json) or our own saved copy. */
        fun parse(o: JSONObject, channel: String? = null, now: Long = System.currentTimeMillis()): AlertMsg {
            val t = o.optJSONObject("texts") ?: JSONObject()
            return AlertMsg(
                alertId = o.getString("alert_id"),
                zoneId = o.optString("zone_id"),
                tier = o.optString("tier", "warning"),
                hazard = o.optString("hazard", "flood"),
                status = o.optString("status", "Actual"),
                texts = t.keys().asSequence().associateWith { t.getString(it) },
                receivedAt = o.optLong("received_at", now),
                channel = channel ?: o.optString("channel", "push"),
            )
        }
    }
}

object Geo {
    private val HIDDEN = listOf("T", "H", "L") // test, heartbeat and load zones are not public

    fun parseZones(geojson: String): List<Zone> {
        val features = JSONObject(geojson).getJSONArray("features")
        return (0 until features.length()).mapNotNull { i ->
            val f = features.getJSONObject(i)
            val p = f.getJSONObject("properties")
            val id = p.getString("zone_id")
            if (p.optBoolean("is_test") || HIDDEN.any { id.startsWith(it) }) return@mapNotNull null
            val g = f.getJSONObject("geometry")
            val polys: List<JSONArray> = when (g.getString("type")) {
                "Polygon" -> listOf(g.getJSONArray("coordinates"))
                "MultiPolygon" -> g.getJSONArray("coordinates").let { mp -> (0 until mp.length()).map { mp.getJSONArray(it) } }
                else -> return@mapNotNull null
            }
            val rings = polys.map { poly ->
                val ring = poly.getJSONArray(0)
                (0 until ring.length()).map { k -> ring.getJSONArray(k).let { LatLon(it.getDouble(1), it.getDouble(0)) } }
            }
            Zone(id, p.optString("name", id), p.optString("district"), p.optString("hazard", "flood"), rings)
        }.sortedWith(compareBy({ it.district }, { it.name }))
    }

    fun parseStates(json: String): Map<String, ZoneState> {
        val zones = JSONObject(json).getJSONArray("zones")
        return (0 until zones.length()).associate { i ->
            val z = zones.getJSONObject(i)
            z.getString("zone_id") to ZoneState(
                z.optString("tier", "normal"), z.optBoolean("blind"),
                if (z.isNull("updated_at")) null else z.optLong("updated_at"),
            )
        }
    }

    fun km(a: LatLon, b: LatLon): Double {
        val dLat = (a.lat - b.lat) * 111.32
        val dLon = (a.lon - b.lon) * 111.32 * cos(Math.toRadians((a.lat + b.lat) / 2))
        return sqrt(dLat * dLat + dLon * dLon)
    }

    /** The zone containing the point, or the nearest one within 3 km; null when outside coverage. */
    fun locate(zones: List<Zone>, p: LatLon): Zone? =
        zones.firstOrNull { it.contains(p) } ?: zones.minByOrNull { km(it.center, p) }?.takeIf { km(it.center, p) <= 3.0 }
}
