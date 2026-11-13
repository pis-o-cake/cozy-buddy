package app.cozybuddy.hub.core.time

import java.time.ZonedDateTime
import javax.inject.Inject

/** 현재 시각의 단일 출처. ViewModel 이 시스템 시계에 직접 묶이지 않게 해 테스트에서 시각을 고정한다. */
interface TimeSource {
    fun now(): ZonedDateTime
}

class SystemTimeSource @Inject constructor() : TimeSource {
    override fun now(): ZonedDateTime = ZonedDateTime.now()
}
