package app.cozybuddy.hub.feature.ambient

import app.cozybuddy.hub.domain.model.DailyWeather
import app.cozybuddy.hub.domain.model.Weather
import java.time.ZoneId
import java.time.ZonedDateTime
import java.util.Locale
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test
import org.junit.jupiter.params.ParameterizedTest
import org.junit.jupiter.params.provider.CsvSource

class AmbientWeatherTest {

    private val now = ZonedDateTime.of(2026, 11, 12, 13, 5, 0, 0, ZoneId.of("Asia/Seoul"))
    private val nowMs = now.toInstant().toEpochMilli()

    private fun weather(observedMs: Long = nowMs, temperature: Double = 12.4, code: Int = 0) = Weather(
        observedEpochMs = observedMs,
        temperatureC = temperature,
        weatherCode = code,
        isDay = true,
        today = DailyWeather(minC = -2.6, maxC = 15.5, precipitationProbabilityPct = 10, weatherCode = code),
        tomorrow = DailyWeather(minC = 0.0, maxC = 8.0, precipitationProbabilityPct = 0, weatherCode = 3),
    )

    @ParameterizedTest
    @CsvSource(
        "0,CLEAR", "1,CLEAR", "2,PARTLY_CLOUDY", "3,CLOUDY", "45,FOG", "48,FOG", "51,DRIZZLE", "55,DRIZZLE",
        "56,RAIN", "61,RAIN", "65,RAIN", "67,RAIN", "71,SNOW", "77,SNOW", "80,RAIN", "82,RAIN", "85,SNOW",
        "95,THUNDER", "99,THUNDER", "4,UNKNOWN", "-1,UNKNOWN", "999,UNKNOWN",
    )
    fun `WMO codes map to display conditions`(code: Int, expected: WeatherCondition) {
        assertEquals(expected, conditionOf(code))
    }

    @Test
    fun `ambient weather rounds temperatures and reads today's range`() {
        val ambient = weather().toAmbient(nowMs)

        assertEquals(AmbientWeather(WeatherCondition.CLEAR, temperature = 12, max = 16, min = -3), ambient)
    }

    @Test
    fun `observation older than the stale limit is hidden`() {
        assertEquals(WeatherCondition.CLEAR, weather(nowMs - WEATHER_STALE_MS).toAmbient(nowMs)?.condition)
        assertNull(weather(nowMs - WEATHER_STALE_MS - 1).toAmbient(nowMs))
    }

    @Test
    fun `ui state carries weather only when provided`() {
        val without = buildAmbientState(now, true, Locale.KOREAN, "M월 d일 EEEE")
        val with = buildAmbientState(now, true, Locale.KOREAN, "M월 d일 EEEE", weather(code = 61))

        assertNull(without.weather)
        assertEquals(WeatherCondition.RAIN, with.weather?.condition)
        assertEquals(12, with.weather?.temperature)
    }
}
