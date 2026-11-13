package app.cozybuddy.hub.domain.model

/** 하루치 예보. [weatherCode] 는 WMO 날씨 코드다. */
data class DailyWeather(
    val minC: Double,
    val maxC: Double,
    val precipitationProbabilityPct: Int,
    val weatherCode: Int,
)

/** 서버가 내려주는 집 위치의 현재 날씨와 오늘·내일 예보. */
data class Weather(
    val observedEpochMs: Long,
    val temperatureC: Double,
    val weatherCode: Int,
    val isDay: Boolean,
    val today: DailyWeather,
    val tomorrow: DailyWeather,
)
