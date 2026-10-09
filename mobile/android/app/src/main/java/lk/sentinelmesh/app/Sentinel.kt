package lk.sentinelmesh.app

import android.app.Application
import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.withContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject

enum class Link { CONNECTING, LISTENING, OFFLINE }

/** App state shared by the screens, the listener service and the alarm. */
object Sentinel {
    private lateinit var app: Application
    private lateinit var prefs: SharedPreferences
    val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    val lang = MutableStateFlow(Lang.EN)
    val myZoneId = MutableStateFlow<String?>(null)
    val onboarded = MutableStateFlow(false)
    val zones = MutableStateFlow<List<Zone>>(emptyList())
    val states = MutableStateFlow<Map<String, ZoneState>>(emptyMap())
    val statesAt = MutableStateFlow(0L)
    val history = MutableStateFlow<List<AlertMsg>>(emptyList())
    val link = MutableStateFlow(Link.CONNECTING)
    val refreshing = MutableStateFlow(false)
    val refreshFailed = MutableStateFlow(false)
    private val _config = MutableStateFlow<AppConfig?>(null)
    val config: StateFlow<AppConfig?> = _config

    @Volatile var appVisible = false

    // A broadcast is shown at most once, whichever way it arrives first (live push or catch-up).
    private val shown = LinkedHashSet<String>()

    fun init(application: Application) {
        app = application
        prefs = application.getSharedPreferences("sentinel", Context.MODE_PRIVATE)
        lang.value = Lang.of(prefs.getString("lang", null))
        myZoneId.value = prefs.getString("zone", null)
        onboarded.value = prefs.getBoolean("onboarded", false)
        shown += prefs.getString("shown", "")!!.split(",").filter { it.isNotBlank() }
        history.value = runCatching {
            val arr = JSONArray(prefs.getString("history", "[]"))
            (0 until arr.length()).map { AlertMsg.parse(arr.getJSONObject(it)) }
        }.getOrDefault(emptyList())
        prefs.getString("zones_cache", null)?.let { runCatching { zones.value = Geo.parseZones(it) } }
        prefs.getString("config_cache", null)?.let { runCatching { _config.value = AppConfig.parse(it) } }
        prefs.getString("states_cache", null)?.let {
            runCatching {
                states.value = Geo.parseStates(it)
                statesAt.value = prefs.getLong("states_at", 0L)
            }
        }
    }

    /** Start network monitoring after local state and notification channels are ready. */
    fun startMonitoring() = scope.launch { refreshLoop() }

    fun setLang(l: Lang) { lang.value = l; prefs.edit().putString("lang", l.code).apply() }

    fun setZone(id: String) {
        myZoneId.value = id
        prefs.edit().putString("zone", id).apply()
        ListenerService.restart(app)
        scope.launch { catchUp() }
    }

    fun finishOnboarding() {
        onboarded.value = true
        prefs.edit().putBoolean("onboarded", true).apply()
        ListenerService.start(app)
    }

    fun identityId(): String? = prefs.getString("identity_id", null)
    fun saveIdentityId(id: String) = prefs.edit().putString("identity_id", id).apply()

    fun zone(id: String?) = zones.value.firstOrNull { it.id == id }
    fun state(id: String?) = states.value[id] ?: ZoneState("normal", false, null)

    /** Config and zone shapes rarely change; the public tiers file refreshes every 30 s while anyone looks. */
    private suspend fun refreshLoop() {
        while (scope.isActive) {
            runCatching {
                if (_config.value == null || zones.value.isEmpty()) loadStatic()
                loadStates()
            }
            delay(if (appVisible) 30_000 else 300_000)
        }
    }

    suspend fun loadStatic(): AppConfig? {
        Net.getText("config.json")?.let { _config.value = AppConfig.parse(it); prefs.edit().putString("config_cache", it).apply() }
        Net.getText("zones.geojson")?.let { zones.value = Geo.parseZones(it); prefs.edit().putString("zones_cache", it).apply() }
        return _config.value
    }

    fun refreshNow() = scope.launch { refresh() }

    suspend fun refresh() = withContext(Dispatchers.IO) {
        if (!refreshing.compareAndSet(false, true)) return@withContext
        refreshFailed.value = false
        try {
            if (zones.value.isEmpty()) loadStatic()
            check(loadStates()) { "Zone status is not available" }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            refreshFailed.value = true
        } finally {
            refreshing.value = false
        }
    }

    private fun loadStates(): Boolean {
        val json = Net.getText("public/zones.json") ?: return false
        json.let {
            states.value = Geo.parseStates(it)
            statesAt.value = System.currentTimeMillis()
            prefs.edit().putString("states_cache", it).putLong("states_at", statesAt.value).apply()
        }
        return true
    }

    /** After (re)connecting, read the zone's last broadcast so nothing sent while offline is missed. */
    suspend fun catchUp() {
        val zone = myZoneId.value ?: return
        runCatching {
            val text = Net.getText("cb/$zone.json") ?: return
            val o = JSONObject(text)
            val ageS = System.currentTimeMillis() / 1000 - o.optLong("broadcast_at")
            if (ageS in 0..6 * 3600) receive(AlertMsg.parse(o, "catchup"))
        }
    }

    /** A broadcast arrived: keep it, and alarm if it is for this phone's area and not seen before. */
    @Synchronized fun receive(msg: AlertMsg) {
        if (!AlertPolicy.accepts(msg, myZoneId.value)) return
        val key = msg.alertId
        synchronized(shown) {
            if (!shown.add(key)) return
            while (shown.size > 200) shown.remove(shown.first())
            prefs.edit().putString("shown", shown.joinToString(",")).apply()
        }
        history.value = (listOf(msg) + history.value).take(50)
        prefs.edit().putString("history", JSONArray(history.value.map { it.toJson() }).toString()).apply()
        if (msg.channel != "test") refreshNow()
        Alarm.raise(app, msg)
    }

    fun testAlarm() {
        val zone = zone(myZoneId.value)
        val name = zone?.name ?: "—"
        receive(
            AlertMsg(
                alertId = "TEST-${System.currentTimeMillis()}", zoneId = zone?.id ?: "", tier = "imminent",
                hazard = zone?.hazard ?: "flood", status = "Exercise",
                texts = mapOf(
                    "en" to "TEST $name: this is how an emergency alert will look and sound. No action needed.",
                    "si" to "පරීක්ෂණය $name: හදිසි අනතුරු ඇඟවීමක් දිස්වන හා ඇසෙන ආකාරය මෙයයි. කිසිවක් කිරීමට අවශ්‍ය නැත.",
                    "ta" to "சோதனை $name: அவசர எச்சரிக்கை இப்படித்தான் தோன்றும், ஒலிக்கும். எதுவும் செய்ய வேண்டியதில்லை.",
                ),
                receivedAt = System.currentTimeMillis(), channel = "test",
            ),
        )
    }
}
