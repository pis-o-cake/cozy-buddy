package app.cozybuddy.hub.data.weather

import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.di.ApplicationScope
import app.cozybuddy.hub.domain.model.DailyWeather
import app.cozybuddy.hub.domain.model.Weather
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.WeatherSnapshot
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import timber.log.Timber
import app.cozybuddy.protocol.v1.WeatherDaily as WeatherDailyProto

/**
 * 서버가 내려주는 날씨의 메모리 사본. 연결 직후와 값이 바뀔 때마다 오는 WeatherSnapshot 이 덮어쓴다.
 *
 * 영속하지 않는다. 서버가 날씨를 모르면(좌표 미설정 등) 아무것도 오지 않아 값은 null 로 남는다.
 */
@Singleton
class WeatherRepository @Inject constructor(
    private val link: HubLink,
    @ApplicationScope private val scope: CoroutineScope,
) {
    private val _state = MutableStateFlow<Weather?>(null)
    val state: StateFlow<Weather?> = _state.asStateFlow()

    /** 수신 구독을 즉시 등록한다. */
    fun start() {
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            link.inbound.collect { message -> handle(message) }
        }
    }

    internal fun handle(message: Envelope) {
        if (message.bodyCase != Envelope.BodyCase.WEATHER_SNAPSHOT) return
        val weather = message.weatherSnapshot.toDomain()
        _state.value = weather
        Timber.i("Weather snapshot: %.1f C code=%d", weather.temperatureC, weather.weatherCode)
    }

    private fun WeatherSnapshot.toDomain() = Weather(
        observedEpochMs = observedEpochMs,
        temperatureC = temperatureC,
        weatherCode = weatherCode,
        isDay = isDay,
        today = today.toDomain(),
        tomorrow = tomorrow.toDomain(),
    )

    private fun WeatherDailyProto.toDomain() = DailyWeather(
        minC = minC,
        maxC = maxC,
        precipitationProbabilityPct = precipitationProbabilityPct,
        weatherCode = weatherCode,
    )
}
