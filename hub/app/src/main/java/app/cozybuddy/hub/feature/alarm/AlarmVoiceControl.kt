package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.di.ApplicationScope
import app.cozybuddy.hub.domain.scheduling.SnoozePolicy
import app.cozybuddy.protocol.v1.AlarmControl
import app.cozybuddy.protocol.v1.AlarmControlAction
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.ErrorCode
import app.cozybuddy.protocol.v1.alarmControlResult
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.hubStatus
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import timber.log.Timber

/**
 * 서버가 보낸 [AlarmControl] 을 오버레이 버튼과 같은 [AlarmRingControl] 경로로 처리하고 결과를 돌려준다.
 *
 * 울리는 알람이 없거나 alarm_id 가 다르면 NOT_FOUND 로 거절한다. ok 는 "정지·스누즈 요청을 서비스에 넘겼다"는
 * 뜻이며 서비스는 곧바로 소리를 끈다.
 */
@Singleton
class AlarmVoiceControl @Inject constructor(
    private val link: HubLink,
    private val holder: RingingStateHolder,
    private val control: AlarmRingControl,
    @ApplicationScope private val scope: CoroutineScope,
) {
    /** 수신 구독을 즉시 등록한다. 연결을 시작하기 전에 호출해야 첫 메시지를 놓치지 않는다. */
    fun start() {
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            link.inbound.collect { message ->
                if (message.bodyCase == Envelope.BodyCase.ALARM_CONTROL) handle(message.alarmControl)
            }
        }
    }

    /** 요청을 적용하고 결과를 서버로 보낸다. */
    internal fun handle(request: AlarmControl) {
        val code = apply(request)
        val ok = code == ErrorCode.ERROR_CODE_UNSPECIFIED
        if (!ok) Timber.w("Alarm control rejected request=%s code=%s", request.requestId, code)
        link.send(
            envelope {
                alarmControlResult = alarmControlResult {
                    requestId = request.requestId
                    alarmId = request.alarmId
                    this.ok = ok
                    this.code = code
                }
            },
        )
    }

    private fun apply(request: AlarmControl): ErrorCode {
        val ringing = holder.state.value as? RingingState.Ringing ?: return ErrorCode.ERROR_CODE_NOT_FOUND
        if (ringing.alarmId != request.alarmId) return ErrorCode.ERROR_CODE_NOT_FOUND
        when (request.action) {
            AlarmControlAction.ALARM_CONTROL_ACTION_STOP -> control.stop()
            AlarmControlAction.ALARM_CONTROL_ACTION_SNOOZE -> {
                val minutes = if (request.snoozeMinutes == 0) SnoozePolicy.DEFAULT_MINUTES else request.snoozeMinutes
                if (minutes !in 1..SnoozePolicy.MAX_MINUTES) return ErrorCode.ERROR_CODE_BAD_REQUEST
                control.snooze(minutes)
            }
            else -> return ErrorCode.ERROR_CODE_BAD_REQUEST
        }
        return ErrorCode.ERROR_CODE_UNSPECIFIED
    }
}

/**
 * 허브 상태([app.cozybuddy.protocol.v1.HubStatus])를 서버에 알린다. 연결 직후와 울림이 시작·종료될 때 보낸다.
 *
 * 볼륨·마이크 음소거·화면 밝기는 허브가 아직 추적하지 않아 채우지 않는다(0/false 는 "모름"이다).
 */
@Singleton
class HubStatusReporter @Inject constructor(
    private val link: HubLink,
    private val holder: RingingStateHolder,
    @ApplicationScope private val scope: CoroutineScope,
) {
    fun start() {
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            val connected = link.state.map { it is ConnectionState.Connected }.distinctUntilChanged()
            val ringingId = holder.state.map { (it as? RingingState.Ringing)?.alarmId.orEmpty() }.distinctUntilChanged()
            combine(connected, ringingId) { isConnected, id -> isConnected to id }
                .collect { (isConnected, id) -> if (isConnected) report(id) }
        }
    }

    private fun report(ringingAlarmId: String) {
        val sent = link.send(envelope { hubStatus = hubStatus { this.ringingAlarmId = ringingAlarmId } })
        if (!sent) Timber.w("Hub status not sent: link is down")
    }
}
