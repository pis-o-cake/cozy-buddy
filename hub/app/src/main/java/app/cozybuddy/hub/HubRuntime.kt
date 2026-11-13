package app.cozybuddy.hub

import app.cozybuddy.hub.core.network.HubConnection
import app.cozybuddy.hub.data.home.HomeRepository
import app.cozybuddy.hub.data.sync.AlarmSyncManager
import app.cozybuddy.hub.data.weather.WeatherRepository
import javax.inject.Inject
import javax.inject.Singleton

/** 프로세스 단위 백그라운드 구성요소를 올바른 순서로 시작한다. */
@Singleton
class HubRuntime @Inject constructor(
    private val connection: HubConnection,
    private val alarmSync: AlarmSyncManager,
    private val home: HomeRepository,
    private val weather: WeatherRepository,
) {
    fun start() {
        // IMPORTANT: 수신자를 먼저 등록해야 연결 직후 오는 AlarmSyncRequest 를 놓치지 않는다.
        alarmSync.start()
        home.start()
        weather.start()
        connection.start()
    }
}
