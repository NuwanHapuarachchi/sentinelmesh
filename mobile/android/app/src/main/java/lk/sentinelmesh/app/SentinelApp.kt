package lk.sentinelmesh.app

import android.app.Application

class SentinelApp : Application() {
    override fun onCreate() {
        super.onCreate()
        Alarm.createChannels(this)
        Sentinel.init(this)
        runCatching { ListenerService.start(this) }
    }
}
