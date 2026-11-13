package app.cozybuddy.hub.feature.alarm

import app.cozybuddy.hub.domain.model.AlarmKind
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

sealed interface RingingState {
    data object Idle : RingingState

    data class Ringing(
        val alarmId: String,
        val kind: AlarmKind,
        val label: String,
        val startedAtEpochMs: Long,
    ) : RingingState
}

/**
 * 울림 상태의 단일 정본. 서비스가 쓰고 Activity 쪽 ViewModel 이 읽는다.
 *
 * 프로세스 안에서만 유효하다. 프로세스가 죽으면 서비스도 함께 죽으므로 Idle 로 시작하는 것이 맞다.
 */
@Singleton
class RingingStateHolder @Inject constructor() {
    private val mutableState = MutableStateFlow<RingingState>(RingingState.Idle)
    val state: StateFlow<RingingState> = mutableState.asStateFlow()

    fun start(ringing: RingingState.Ringing) {
        mutableState.value = ringing
    }

    fun clear() {
        mutableState.value = RingingState.Idle
    }
}

/** UI 가 울림을 끄는 통로. 서비스 구현은 플랫폼에 묶여 있어 인터페이스로 가린다. */
interface AlarmRingControl {
    fun stop()
    fun snooze()
}
