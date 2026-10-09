package lk.sentinelmesh.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ZoneTest {
    private fun square(lat: Double, lon: Double) = listOf(
        LatLon(lat, lon), LatLon(lat, lon + 0.01),
        LatLon(lat + 0.01, lon + 0.01), LatLon(lat + 0.01, lon), LatLon(lat, lon),
    )

    private val zone = Zone("Z07", "Test area", "Kegalle", "flood", listOf(square(7.0, 80.0), square(7.1, 80.1)))

    @Test fun `locates every part of a multi polygon zone`() {
        assertTrue(zone.contains(LatLon(7.005, 80.005)))
        assertTrue(zone.contains(LatLon(7.105, 80.105)))
        assertEquals(zone, Geo.locate(listOf(zone), LatLon(7.105, 80.105)))
        assertFalse(zone.contains(LatLon(7.05, 80.05)))
    }

    @Test fun `does not choose an area outside coverage`() {
        assertNull(Geo.locate(listOf(zone), LatLon(8.0, 81.0)))
        assertNull(Geo.locate(emptyList(), LatLon(7.0, 80.0)))
    }

    @Test fun `uses nearby zone when point is just outside boundary`() {
        assertEquals(zone, Geo.locate(listOf(zone), LatLon(7.015, 80.005)))
    }
}
