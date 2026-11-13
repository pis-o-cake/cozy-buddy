package app.cozybuddy.hub.domain.usecase

import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.domain.model.Alarm
import app.cozybuddy.hub.domain.model.AlarmSchedule
import app.cozybuddy.hub.domain.model.Snooze
import app.cozybuddy.hub.domain.repository.AlarmRepository
import app.cozybuddy.hub.domain.repository.SnoozeRepository
import app.cozybuddy.hub.domain.scheduling.AlarmScheduler
import app.cozybuddy.hub.domain.scheduling.NextFireCalculator
import app.cozybuddy.hub.domain.scheduling.SnoozePolicy
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.first
import timber.log.Timber

/** 저장소와 [AlarmScheduler] 를 묶어 "DB 의 상태 == 예약된 상태" 를 유지한다. */
@Singleton
class AlarmCoordinator @Inject constructor(
    private val repository: AlarmRepository,
    private val snoozes: SnoozeRepository,
    private val scheduler: AlarmScheduler,
    private val timeSource: TimeSource,
) {

    /** 저장 후 예약을 맞춘다. 울릴 일이 없는 일회성은 비활성으로 정규화해 저장한다. */
    suspend fun save(alarm: Alarm): Alarm {
        val now = timeSource.now()
        val normalized = if (alarm.enabled && NextFireCalculator.nextFire(alarm, now) == null) {
            alarm.copy(enabled = false)
        } else {
            alarm
        }
        val stored = repository.upsert(normalized)
        applySchedule(stored)
        return stored
    }

    suspend fun delete(id: String) {
        repository.delete(id)
        snoozes.clear(id)
        scheduler.cancel(id)
    }

    suspend fun setEnabled(id: String, enabled: Boolean) {
        val alarm = repository.get(id) ?: return
        if (alarm.enabled == enabled) return
        save(alarm.copy(enabled = enabled))
    }

    /** 부팅·시각 변경·앱 업데이트 뒤 전체 재예약. 오디오는 건드리지 않는다. */
    suspend fun rescheduleAll() {
        val alarms = repository.observeAll().first()
        Timber.i("Rescheduling %d alarms", alarms.size)
        alarms.forEach { alarm ->
            if (alarm.enabled && NextFireCalculator.nextFire(alarm, timeSource.now()) == null) {
                // 꺼져 있던 동안 지나간 일회성은 울리지 않고 비활성화한다.
                Timber.i("Disabling expired one-shot id=%s", alarm.id)
                repository.upsert(alarm.copy(enabled = false))
                snoozes.clear(alarm.id)
                scheduler.cancel(alarm.id)
                return@forEach
            }
            applySchedule(alarm)
        }
        restoreSnoozes(alarms.filter { it.enabled }.map { it.id }.toSet())
    }

    /**
     * 정규 슬롯과 별개인 스누즈 슬롯을 복구한다.
     *
     * 꺼져 있던 동안 지나간 스누즈는 늦게 울리지 않고 버린다(놓친 알람으로 로그만 남김).
     */
    private suspend fun restoreSnoozes(activeIds: Set<String>) {
        val now = timeSource.now().toInstant()
        snoozes.getAll().forEach { snooze ->
            if (snooze.alarmId !in activeIds) {
                Timber.i("Dropping snooze of missing or disabled alarm id=%s", snooze.alarmId)
                snoozes.clear(snooze.alarmId)
                return@forEach
            }
            if (!snooze.fireAt.isAfter(now)) {
                Timber.w("Missed snooze expired while offline id=%s at=%s", snooze.alarmId, snooze.fireAt)
                snoozes.clear(snooze.alarmId)
                return@forEach
            }
            scheduler.scheduleSnooze(snooze.alarmId, snooze.fireAt)
            Timber.i("Snooze restored id=%s at=%s", snooze.alarmId, snooze.fireAt)
        }
    }

    /**
     * 울림이 시작될 때 호출한다. 반복 알람은 바로 다음 회차를 걸어 두어, 서비스가 도중에 죽어도 다음 날 울리게 한다.
     */
    suspend fun onFired(id: String): Alarm? {
        val alarm = repository.get(id) ?: return null
        // 스누즈 슬롯이 울린 것이므로 저장된 스누즈는 소비됐다.
        snoozes.clear(id)
        if (alarm.schedule is AlarmSchedule.Repeating) applySchedule(alarm)
        return alarm
    }

    /**
     * 울림이 끝났을 때 호출한다.
     *
     * 스누즈면 별도 슬롯에 [SnoozePolicy] 시각을 예약하고, 아니면 일회성은 비활성화한다.
     */
    suspend fun onRingFinished(id: String, snoozed: Boolean) {
        val alarm = repository.get(id)
        if (alarm == null) {
            snoozes.clear(id)
            return
        }
        if (snoozed) {
            val fireAt = SnoozePolicy.snoozeAt(timeSource.now().toInstant())
            snoozes.put(Snooze(id, fireAt))
            scheduler.scheduleSnooze(id, fireAt)
            return
        }
        snoozes.clear(id)
        if (alarm.schedule is AlarmSchedule.OneShot && alarm.enabled) {
            repository.upsert(alarm.copy(enabled = false))
            scheduler.cancel(id)
        }
    }

    private suspend fun applySchedule(alarm: Alarm) {
        val triggerAt = NextFireCalculator.nextFire(alarm, timeSource.now())
        if (triggerAt == null) {
            snoozes.clear(alarm.id)
            scheduler.cancel(alarm.id)
            return
        }
        scheduler.schedule(alarm.id, triggerAt)
    }
}
