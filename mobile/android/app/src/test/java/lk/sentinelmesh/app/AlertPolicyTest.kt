package lk.sentinelmesh.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AlertPolicyTest {
    private val now = 1_800_000_000_000L
    private val message = AlertMsg("SM-1", "Z07", "warning", "flood", "Actual", mapOf("en" to "Move to safety"), now, "push", now / 1000)

    @Test fun `accepts current warning and all clear for selected area`() {
        assertTrue(AlertPolicy.accepts(message, "Z07", now))
        assertTrue(AlertPolicy.accepts(message.copy(tier = "clear"), "Z07", now))
        assertFalse(AlertPolicy.accepts(message, "Z08", now))
    }

    @Test fun `rejects stale queued alerts and missing or future timestamps`() {
        assertFalse(AlertPolicy.accepts(message.copy(broadcastAt = now / 1000 - 21_601), "Z07", now))
        assertFalse(AlertPolicy.accepts(message.copy(broadcastAt = null), "Z07", now))
        assertFalse(AlertPolicy.accepts(message.copy(broadcastAt = now / 1000 + 1), "Z07", now))
        assertTrue(AlertPolicy.accepts(message.copy(broadcastAt = now / 1000 - 21_600), "Z07", now))
    }

    @Test fun `rejects test heartbeat and invalid messages but permits explicit local exercise`() {
        assertFalse(AlertPolicy.accepts(message.copy(status = "Test"), "Z07", now))
        assertFalse(AlertPolicy.accepts(message.copy(status = "Draft"), "Z07", now))
        assertFalse(AlertPolicy.accepts(message.copy(tier = "unknown"), "Z07", now))
        assertFalse(AlertPolicy.accepts(message.copy(alertId = ""), "Z07", now))
        assertTrue(AlertPolicy.accepts(message.copy(status = "Exercise", channel = "test", broadcastAt = null), "Z07", now))
    }
}
