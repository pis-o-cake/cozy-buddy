package app.cozybuddy.hub.feature.alarm.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import app.cozybuddy.hub.domain.usecase.AlarmCoordinator
import dagger.hilt.android.AndroidEntryPoint
import javax.inject.Inject
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import timber.log.Timber

/**
 * 부팅·시각·시간대·앱 교체·정확 알람 권한 변경 뒤 예약을 복구한다.
 *
 * IMPORTANT: 여기서는 오디오를 재생하거나 FGS 를 시작하지 않는다. Android 17 은 BOOT_COMPLETED 에서
 * 시작된 작업의 오디오를 막는다.
 */
@AndroidEntryPoint
class AlarmRescheduleReceiver : HiltBroadcastReceiver() {

    @Inject lateinit var coordinator: AlarmCoordinator

    override fun onReceive(context: Context, intent: Intent) {
        super.onReceive(context, intent)
        if (intent.action !in HANDLED_ACTIONS) return
        Timber.i("Reschedule trigger action=%s", intent.action)
        val pending = goAsync()
        CoroutineScope(SupervisorJob() + Dispatchers.IO).launch {
            try {
                coordinator.rescheduleAll()
            } catch (e: Exception) {
                Timber.e(e, "Reschedule failed")
            } finally {
                pending.finish()
            }
        }
    }

    private companion object {
        // 문자열 리터럴인 이유: ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED 는 API 31 상수라 minSdk 29 에서 직접 못 쓴다.
        val HANDLED_ACTIONS = setOf(
            Intent.ACTION_BOOT_COMPLETED,
            Intent.ACTION_TIME_CHANGED,
            Intent.ACTION_TIMEZONE_CHANGED,
            Intent.ACTION_MY_PACKAGE_REPLACED,
            "android.app.action.SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED",
        )
    }
}
