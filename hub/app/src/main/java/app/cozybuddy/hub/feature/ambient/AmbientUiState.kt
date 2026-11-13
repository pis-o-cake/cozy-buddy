package app.cozybuddy.hub.feature.ambient

import java.time.LocalTime
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/**
 * 앰비언트 정책 상수.
 *
 * TODO: 조도 센서가 확보되면 시간대 규칙을 센서 값으로 대체한다.
 */
object AmbientConfig {
    val NIGHT_START: LocalTime = LocalTime.of(22, 0)
    val NIGHT_END: LocalTime = LocalTime.of(7, 0)

    /** 창 밝기 비율. 시스템 기본 밝기를 쓰려면 음수(BRIGHTNESS_OVERRIDE_NONE). */
    const val BRIGHTNESS_SYSTEM = -1f
    const val BRIGHTNESS_NIGHT = 0.05f
    const val CONTENT_ALPHA_DAY = 1f
    const val CONTENT_ALPHA_NIGHT = 0.45f

    /** 번인 방지 이동 경로(dp). 중앙에서 시작해 한 바퀴 돈다. */
    val PIXEL_SHIFT_CYCLE: List<PixelShift> = listOf(
        PixelShift(0, 0),
        PixelShift(4, 0),
        PixelShift(4, 4),
        PixelShift(0, 4),
        PixelShift(-4, 4),
        PixelShift(-4, 0),
        PixelShift(-4, -4),
        PixelShift(0, -4),
        PixelShift(4, -4),
    )
}

enum class DimLevel(val windowBrightness: Float, val contentAlpha: Float) {
    NONE(AmbientConfig.BRIGHTNESS_SYSTEM, AmbientConfig.CONTENT_ALPHA_DAY),
    NIGHT(AmbientConfig.BRIGHTNESS_NIGHT, AmbientConfig.CONTENT_ALPHA_NIGHT),
}

/** 앰비언트 화면 구석의 서버 연결 표시. 정상일 때는 아무것도 그리지 않는다. */
enum class ConnectionIndicator { NONE, OFFLINE, AUTH_FAILED }

data class PixelShift(val xDp: Int, val yDp: Int)

data class AmbientUiState(
    /** 시·분 네 자리. 항상 0~9 네 개이며 12시간제에서도 앞자리를 0으로 채워 시계가 중앙에 고정되게 한다. */
    val digits: List<Int>,
    val dateText: String,
    val dim: DimLevel,
    val pixelShift: PixelShift,
) {
    companion object {
        val Initial = AmbientUiState(
            digits = listOf(0, 0, 0, 0),
            dateText = "",
            dim = DimLevel.NONE,
            pixelShift = PixelShift(0, 0),
        )
    }
}

internal fun buildAmbientState(
    now: ZonedDateTime,
    is24Hour: Boolean,
    locale: Locale,
    datePattern: String,
): AmbientUiState {
    val hour = if (is24Hour) now.hour else (now.hour % 12).let { if (it == 0) 12 else it }
    val minute = now.minute
    return AmbientUiState(
        digits = listOf(hour / 10, hour % 10, minute / 10, minute % 10),
        dateText = DateTimeFormatter.ofPattern(datePattern, locale).format(now),
        dim = dimLevelAt(now.toLocalTime()),
        pixelShift = pixelShiftAt(now),
    )
}

internal fun dimLevelAt(time: LocalTime): DimLevel {
    val isNight = time >= AmbientConfig.NIGHT_START || time < AmbientConfig.NIGHT_END
    return if (isNight) DimLevel.NIGHT else DimLevel.NONE
}

// 분 단위로 경로를 한 칸씩 전진시킨다. 상태를 저장하지 않으므로 재시작해도 같은 시각이면 같은 위치다.
internal fun pixelShiftAt(now: ZonedDateTime): PixelShift {
    val cycle = AmbientConfig.PIXEL_SHIFT_CYCLE
    val minuteOfEpoch = now.toInstant().toEpochMilli() / MILLIS_PER_MINUTE
    return cycle[(minuteOfEpoch % cycle.size).toInt()]
}

internal const val MILLIS_PER_MINUTE = 60_000L
