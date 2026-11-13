package app.cozybuddy.hub.data.weather

import app.cozybuddy.hub.core.network.FakeHubLink
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.weatherDaily
import app.cozybuddy.protocol.v1.weatherSnapshot
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test

class WeatherRepositoryTest {

    private val link = FakeHubLink()

    private fun snapshot(temperature: Double) = envelope {
        weatherSnapshot = weatherSnapshot {
            observedEpochMs = 1_700_000_000_000
            temperatureC = temperature
            weatherCode = 61
            isDay = true
            today = weatherDaily { minC = -3.0; maxC = 5.5; precipitationProbabilityPct = 80; weatherCode = 61 }
            tomorrow = weatherDaily { minC = 1.0; maxC = 9.0; precipitationProbabilityPct = 10; weatherCode = 3 }
        }
    }

    @Test
    fun `starts empty and snapshots replace the previous value`() = runTest {
        val repository = WeatherRepository(link, backgroundScope)
        repository.start()
        runCurrent()
        assertNull(repository.state.value)

        link.receive(snapshot(12.5))
        runCurrent()
        val first = repository.state.value!!
        assertEquals(12.5, first.temperatureC)
        assertEquals(61, first.weatherCode)
        assertEquals(-3.0, first.today.minC)
        assertEquals(80, first.today.precipitationProbabilityPct)
        assertEquals(3, first.tomorrow.weatherCode)

        link.receive(snapshot(13.0))
        runCurrent()
        assertEquals(13.0, repository.state.value!!.temperatureC)
    }

    @Test
    fun `other messages leave weather untouched`() = runTest {
        val repository = WeatherRepository(link, backgroundScope)
        repository.start()
        runCurrent()

        link.receive(envelope { })
        runCurrent()

        assertNull(repository.state.value)
    }
}
