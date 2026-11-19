package app.cozybuddy.hub

import app.cozybuddy.hub.core.network.HubConnection
import app.cozybuddy.hub.data.home.HomeRepository
import app.cozybuddy.hub.data.sync.AlarmSyncManager
import app.cozybuddy.hub.data.weather.WeatherRepository
import app.cozybuddy.hub.feature.alarm.AlarmVoiceControl
import app.cozybuddy.hub.di.ApplicationScope
import app.cozybuddy.hub.domain.usecase.AlarmCoordinator
import app.cozybuddy.hub.feature.alarm.HubStatusReporter
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.launch
import timber.log.Timber

/** 프로세스 단위 백그라운드 구성요소를 올바른 순서로 시작한다. */
@Singleton
class HubRuntime @Inject constructor(
    private val connection: HubConnection,
    private val alarmSync: AlarmSyncManager,
    private val home: HomeRepository,
    private val weather: WeatherRepository,
    private val alarmVoiceControl: AlarmVoiceControl,
    private val hubStatus: HubStatusReporter,
    private val alarmCoordinator: AlarmCoordinator,
    @ApplicationScope private val scope: CoroutineScope,
) {
    /**
     * 프로세스 시작 시 한 번 호출한다.
     *
     * 강제 종료 뒤에는 OS 가 알람 예약을 모두 지우므로, DB 상태로 예약을 다시 맞춘다.
     */
    fun start() {
        scope.launch {
            try {
                alarmCoordinator.rescheduleAll()
            } catch (e: Exception) {
                Timber.e(e, "Alarm reschedule at startup failed")
            }
        }
        // IMPORTANT: 수신자를 먼저 등록해야 연결 직후 오는 AlarmSyncRequest 를 놓치지 않는다.
        alarmSync.start()
        alarmVoiceControl.start()
        hubStatus.start()
        home.start()
        weather.start()
        connection.start()
    }
}
