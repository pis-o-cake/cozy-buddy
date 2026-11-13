package app.cozybuddy.hub.feature.ambient

import androidx.annotation.StringRes
import app.cozybuddy.hub.R
import app.cozybuddy.hub.domain.model.Weather
import kotlin.math.roundToInt

/** 앰비언트에 보이는 날씨 상태 단어. WMO 코드를 화면용 몇 가지로 묶는다. */
enum class WeatherCondition(@StringRes val label: Int) {
    CLEAR(R.string.weather_condition_clear),
    PARTLY_CLOUDY(R.string.weather_condition_partly_cloudy),
    CLOUDY(R.string.weather_condition_cloudy),
    FOG(R.string.weather_condition_fog),
    DRIZZLE(R.string.weather_condition_drizzle),
    RAIN(R.string.weather_condition_rain),
    SNOW(R.string.weather_condition_snow),
    THUNDER(R.string.weather_condition_thunder),
    UNKNOWN(R.string.weather_condition_unknown),
}

/** 앰비언트 하단 날씨 줄에 그릴 값. 온도는 이미 반올림한 정수다. */
data class AmbientWeather(
    val condition: WeatherCondition,
    val temperature: Int,
    val max: Int,
    val min: Int,
)

/** 이 시간보다 오래된 관측은 보여주지 않는다. 서버 weather_stale_seconds 와 같은 값이다. */
internal const val WEATHER_STALE_MS = 3 * 60 * 60 * 1000L

/** WMO 날씨 코드를 [WeatherCondition] 으로 바꾼다. 모르는 코드는 [WeatherCondition.UNKNOWN]. */
internal fun conditionOf(weatherCode: Int): WeatherCondition = when (weatherCode) {
    0, 1 -> WeatherCondition.CLEAR
    2 -> WeatherCondition.PARTLY_CLOUDY
    3 -> WeatherCondition.CLOUDY
    45, 48 -> WeatherCondition.FOG
    51, 53, 55 -> WeatherCondition.DRIZZLE
    56, 57, 61, 63, 65, 66, 67, 80, 81, 82 -> WeatherCondition.RAIN
    71, 73, 75, 77, 85, 86 -> WeatherCondition.SNOW
    95, 96, 99 -> WeatherCondition.THUNDER
    else -> WeatherCondition.UNKNOWN
}

/** 관측이 [WEATHER_STALE_MS] 안이면 화면용 값으로, 아니면 null. */
internal fun Weather.toAmbient(nowEpochMs: Long): AmbientWeather? {
    if (nowEpochMs - observedEpochMs > WEATHER_STALE_MS) return null
    return AmbientWeather(
        condition = conditionOf(weatherCode),
        temperature = temperatureC.roundToInt(),
        max = today.maxC.roundToInt(),
        min = today.minC.roundToInt(),
    )
}
