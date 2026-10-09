package lk.sentinelmesh.app

import android.app.Application
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.media.AudioAttributes
import android.media.RingtoneManager
import androidx.core.app.NotificationCompat

/** Notification channels and the alarm itself. */
object Alarm {
    private const val CH_EMERGENCY = "sm_emergency"
    private const val CH_UPDATES = "sm_updates"
    private const val CH_LISTENER = "sm_listener"
    const val LISTENER_ID = 1
    private const val ALERT_ID = 2
    const val EXTRA_ALERT = "alert"

    fun createChannels(app: Application) {
        val nm = app.getSystemService(NotificationManager::class.java)
        // The alarm sound stream rings even when the ringer is on silent, and alarms pass Do Not Disturb by default.
        val alarmAudio = AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
        nm.createNotificationChannel(NotificationChannel(CH_EMERGENCY, "Emergency alerts", NotificationManager.IMPORTANCE_HIGH).apply {
            description = "Warnings and emergencies for your area: full screen, with an alarm"
            setSound(RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM), alarmAudio)
            enableVibration(true)
            vibrationPattern = longArrayOf(0, 800, 300, 800, 300, 1500)
            enableLights(true)
            lightColor = Color.RED
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
            setBypassDnd(true)
        })
        nm.createNotificationChannel(NotificationChannel(CH_UPDATES, "Advisories and all-clears", NotificationManager.IMPORTANCE_DEFAULT).apply {
            description = "Rising risk and all-clear messages"
            lockscreenVisibility = Notification.VISIBILITY_PUBLIC
        })
        nm.createNotificationChannel(NotificationChannel(CH_LISTENER, "Listening for alerts", NotificationManager.IMPORTANCE_MIN).apply {
            description = "Shown while the app keeps its connection to the warning service"
            setShowBadge(false)
        })
    }

    private fun openApp(ctx: Context) = PendingIntent.getActivity(
        ctx, 0, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    fun listenerNotification(ctx: Context, link: Link): Notification {
        val s = strings(Sentinel.lang.value)
        val zone = Sentinel.zone(Sentinel.myZoneId.value)
        val state = when (link) {
            Link.LISTENING -> s.listening
            Link.CONNECTING -> s.connecting
            Link.OFFLINE -> s.offline
        }
        return NotificationCompat.Builder(ctx, CH_LISTENER)
            .setSmallIcon(R.drawable.ic_stat_alert)
            .setContentTitle(zone?.name ?: ctx.getString(R.string.app_name))
            .setContentText(state)
            .setOngoing(true)
            .setSilent(true)
            .setColor(0xFF2F6F8F.toInt())
            .setContentIntent(openApp(ctx))
            .setForegroundServiceBehavior(NotificationCompat.FOREGROUND_SERVICE_IMMEDIATE)
            .build()
    }

    fun updateListener(ctx: Context, link: Link) {
        ctx.getSystemService(NotificationManager::class.java).notify(LISTENER_ID, listenerNotification(ctx, link))
    }

    fun raise(ctx: Context, msg: AlertMsg) {
        val s = strings(Sentinel.lang.value)
        val zone = Sentinel.zone(msg.zoneId)
        val title = (s.headline[msg.tier] ?: s.headline.getValue("warning")) +
            (if (msg.status != "Actual") " · ${s.exercise}" else "") + (zone?.let { " · ${it.name}" } ?: "")
        val nm = ctx.getSystemService(NotificationManager::class.java)
        if (!msg.urgent) {
            nm.notify(ALERT_ID, NotificationCompat.Builder(ctx, CH_UPDATES)
                .setSmallIcon(R.drawable.ic_stat_alert).setContentTitle(title).setContentText(msg.text(Sentinel.lang.value))
                .setStyle(NotificationCompat.BigTextStyle().bigText(msg.text(Sentinel.lang.value)))
                .setColor(if (msg.tier == "clear") 0xFF2E7D4F.toInt() else 0xFFE8913A.toInt())
                .setAutoCancel(true).setContentIntent(openApp(ctx)).build())
            return
        }
        val alertIntent = Intent(ctx, AlertActivity::class.java)
            .putExtra(EXTRA_ALERT, msg.toJson().toString())
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val full = PendingIntent.getActivity(ctx, 1, alertIntent, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        val n = NotificationCompat.Builder(ctx, CH_EMERGENCY)
            .setSmallIcon(R.drawable.ic_stat_alert)
            .setContentTitle(title)
            .setContentText(msg.text(Sentinel.lang.value))
            .setStyle(NotificationCompat.BigTextStyle().bigText(msg.text(Sentinel.lang.value)))
            .setCategory(NotificationCompat.CATEGORY_ALARM)
            .setPriority(NotificationCompat.PRIORITY_MAX)
            .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
            .setColor(if (msg.tier == "imminent") 0xFF7D1220.toInt() else 0xFFD14B3D.toInt())
            .setColorized(true)
            .setOngoing(msg.tier == "imminent")
            .setContentIntent(full)
            .setFullScreenIntent(full, true)
            .build()
        // An emergency keeps ringing until the person opens it.
        if (msg.tier == "imminent") n.flags = n.flags or Notification.FLAG_INSISTENT
        nm.notify(ALERT_ID, n)
        // With the app open, show the alarm screen straight away.
        if (Sentinel.appVisible) ctx.startActivity(alertIntent)
    }

    fun silence(ctx: Context) = ctx.getSystemService(NotificationManager::class.java).cancel(ALERT_ID)
}
