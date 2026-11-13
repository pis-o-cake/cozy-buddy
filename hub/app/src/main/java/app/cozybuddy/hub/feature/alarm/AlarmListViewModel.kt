package app.cozybuddy.hub.feature.alarm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.domain.repository.AlarmRepository
import app.cozybuddy.hub.domain.scheduling.AlarmScheduler
import app.cozybuddy.hub.domain.usecase.AlarmCoordinator
import app.cozybuddy.hub.domain.usecase.AlarmFactory
import app.cozybuddy.hub.feature.ambient.ClockDisplayConfig
import dagger.hilt.android.lifecycle.HiltViewModel
import java.time.DayOfWeek
import java.time.format.TextStyle
import java.util.UUID
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch
import timber.log.Timber

@HiltViewModel
class AlarmListViewModel @Inject constructor(
    repository: AlarmRepository,
    private val coordinator: AlarmCoordinator,
    private val scheduler: AlarmScheduler,
    private val timeSource: TimeSource,
    private val displayConfig: ClockDisplayConfig,
) : ViewModel() {

    private val exactAlarmGranted = MutableStateFlow(scheduler.canScheduleExact())

    val uiState: StateFlow<AlarmListUiState> = combine(repository.observeAll(), exactAlarmGranted) { alarms, granted ->
        val zone = timeSource.now().zone
        val is24Hour = displayConfig.is24Hour()
        val locale = displayConfig.locale()
        AlarmListUiState(
            items = alarms.map { it.toItemUi(zone, is24Hour, locale) },
            exactAlarmGranted = granted,
            is24Hour = is24Hour,
            dayOptions = DayOfWeek.entries.map { DayOption(it, it.getDisplayName(TextStyle.SHORT, locale)) },
        )
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), AlarmListUiState())

    /** 설정 화면에서 돌아올 때 권한 상태를 다시 읽는다. */
    fun refreshExactAlarmState() {
        exactAlarmGranted.value = scheduler.canScheduleExact()
    }

    fun onToggle(id: String, enabled: Boolean) = launchSafely("toggle") { coordinator.setEnabled(id, enabled) }

    fun onDelete(id: String) = launchSafely("delete") { coordinator.delete(id) }

    fun onAddAlarm(hour: Int, minute: Int, days: Set<DayOfWeek>) = launchSafely("add alarm") {
        coordinator.save(AlarmFactory.newAlarm(newId(), hour, minute, days, timeSource.now()))
    }

    fun onAddQuickTimer() = launchSafely("add timer") {
        coordinator.save(AlarmFactory.newTimer(newId(), QUICK_TIMER_MINUTES, timeSource.now()))
    }

    private fun newId(): String = UUID.randomUUID().toString()

    private fun launchSafely(what: String, block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
            } catch (e: Exception) {
                Timber.e(e, "Alarm list action failed: %s", what)
            }
        }
    }

    private companion object {
        const val STOP_TIMEOUT_MS = 5_000L
        const val QUICK_TIMER_MINUTES = 1L
    }
}
