package lk.sentinelmesh.app

import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.json.JSONObject
import kotlin.random.Random

/**
 * Keeps one listen-only MQTT connection to AWS IoT Core, subscribed to sm/broadcast/{my zone}, so a
 * warning pops up even when the app is closed. Reconnects with a random back-off so that, after an
 * outage, phones don't all reconnect in the same second.
 */
class ListenerService : Service() {
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loop: Job? = null
    private var watcher: Job? = null
    @Volatile private var current: MqttListener? = null
    @Volatile private var wake = CompletableDeferred<Unit>()
    private var creds: AwsCredentials? = null
    private val netCallback = object : ConnectivityManager.NetworkCallback() {
        override fun onAvailable(network: Network) { wake.complete(Unit) }
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
        ServiceCompat.startForeground(this, Alarm.LISTENER_ID, Alarm.listenerNotification(this, Sentinel.link.value), type)
        getSystemService(ConnectivityManager::class.java).registerDefaultNetworkCallback(netCallback)
        loop = serviceScope.launch { run() }
        watcher = serviceScope.launch {
            Sentinel.link.collect { Alarm.updateListener(this@ListenerService, it) }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_RESTART) {
            current?.close()
            wake.complete(Unit)
        }
        return START_STICKY
    }

    override fun onDestroy() {
        runCatching { getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(netCallback) }
        loop?.cancel()
        watcher?.cancel()
        serviceScope.cancel()
        current?.close()
        super.onDestroy()
    }

    private suspend fun run() {
        var failures = 0
        while (serviceScope.isActive) {
            val zone = Sentinel.myZoneId.value
            if (zone == null) { delay(5_000); continue }
            Sentinel.link.value = Link.CONNECTING
            val closed = CompletableDeferred<String>()
            val ok = try {
                connect(zone, closed)
                true
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Log.w(TAG, "connect failed", e)
                false
            }
            if (ok) {
                val reason = closed.await()
                Log.i(TAG, "connection ended: $reason")
                if (Sentinel.link.value == Link.LISTENING) failures = 0
            }
            failures++
            Sentinel.link.value = Link.OFFLINE
            // A zone change should subscribe to the new area without waiting for outage back-off.
            if (zone != Sentinel.myZoneId.value) continue
            // Exponential back-off with full jitter, capped at 2 minutes; a new network wakes it early.
            val capMs = minOf(120_000L, 2_000L shl minOf(failures, 6))
            wake = CompletableDeferred()
            withTimeoutOrNull(Random.nextLong(1_000, capMs)) { wake.await() }
        }
    }

    private suspend fun connect(zone: String, closed: CompletableDeferred<String>) {
        // A cached config from before the app endpoint existed: fetch a fresh one.
        val cfg = Sentinel.config.value?.takeIf { it.identityPoolId.isNotBlank() && it.iotEndpoint.isNotBlank() }
            ?: Sentinel.loadStatic() ?: error("no config")
        require(cfg.identityPoolId.isNotBlank() && cfg.iotEndpoint.isNotBlank()) { "config has no app endpoint" }
        val id = Sentinel.identityId() ?: Cognito.identityId(cfg, null).also { Sentinel.saveIdentityId(it) }
        val c = creds?.takeIf { it.expiresAtMs - System.currentTimeMillis() > 10 * 60_000 }
            ?: runCatching { Cognito.credentials(cfg, id) }.getOrElse {
                // The guest pool was rebuilt: start over with a fresh identity.
                val fresh = Cognito.identityId(cfg, null).also { n -> Sentinel.saveIdentityId(n) }
                Cognito.credentials(cfg, fresh)
            }
        creds = c
        val clientId = Sentinel.identityId()!!
        val listener = MqttListener(
            url = SigV4.iotWebSocketUrl(cfg.iotEndpoint, cfg.region, c),
            clientId = clientId,
            topics = listOf("sm/broadcast/$zone"),
            keepAliveS = KEEP_ALIVE_S,
            onReady = {
                Sentinel.link.value = Link.LISTENING
                serviceScope.launch { Sentinel.catchUp() }
            },
            onMessage = { _, payload ->
                runCatching { Sentinel.receive(AlertMsg.parse(JSONObject(String(payload, Charsets.UTF_8)), "push")) }
                    .onFailure { Log.w(TAG, "bad message", it) }
            },
            onClosed = { closed.complete(it) },
        )
        current = listener
        listener.open()
        // Keep-alive pings (free on AWS), and reconnect with fresh credentials before they expire.
        serviceScope.launch {
            while (!closed.isCompleted) {
                delay(PING_EVERY_MS)
                if (closed.isCompleted) break
                if (c.expiresAtMs - System.currentTimeMillis() < 5 * 60_000) { listener.close(); break }
                listener.ping()
            }
        }
    }

    companion object {
        private const val TAG = "SentinelListener"
        private const val KEEP_ALIVE_S = 300
        private const val PING_EVERY_MS = 240_000L
        const val ACTION_RESTART = "lk.sentinelmesh.app.RESTART"

        fun start(ctx: Context) {
            if (!Sentinel.onboarded.value) return
            ContextCompat.startForegroundService(ctx, Intent(ctx, ListenerService::class.java))
        }

        fun restart(ctx: Context) {
            if (!Sentinel.onboarded.value) return
            ContextCompat.startForegroundService(ctx, Intent(ctx, ListenerService::class.java).setAction(ACTION_RESTART))
        }
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        runCatching { ListenerService.start(context) }
    }
}
