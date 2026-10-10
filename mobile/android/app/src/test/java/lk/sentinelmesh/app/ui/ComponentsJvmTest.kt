package lk.sentinelmesh.app.ui

import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

@RunWith(RobolectricTestRunner::class)
@Config(sdk = [34], qualifiers = "w411dp-h891dp-xhdpi", application = UiTestApplication::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class ComponentsJvmTest : UiScenarios()

/** Initialize local preferences without starting services or downloading live warning data. */
class UiTestApplication : android.app.Application() {
    override fun onCreate() {
        super.onCreate()
        lk.sentinelmesh.app.Sentinel.init(this)
        lk.sentinelmesh.app.Alarm.createChannels(this)
    }
}
