package app.cozybuddy.hub.feature.ambient

import android.content.Context
import android.text.format.DateFormat
import app.cozybuddy.hub.R
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.Locale
import javax.inject.Inject

/** 시계 표시에 영향을 주는 기기 설정. 사용자가 시스템 설정을 바꾸면 다음 틱에 반영된다. */
interface ClockDisplayConfig {
    fun is24Hour(): Boolean
    fun locale(): Locale

    /** java.time 패턴. 로캘별 순서는 리소스가 정한다. */
    fun datePattern(): String
}

class AndroidClockDisplayConfig @Inject constructor(
    @ApplicationContext private val context: Context,
) : ClockDisplayConfig {
    override fun is24Hour(): Boolean = DateFormat.is24HourFormat(context)
    override fun locale(): Locale = context.resources.configuration.locales[0]
    override fun datePattern(): String = context.getString(R.string.ambient_date_pattern)
}
