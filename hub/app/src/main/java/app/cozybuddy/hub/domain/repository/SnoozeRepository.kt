package app.cozybuddy.hub.domain.repository

import app.cozybuddy.hub.domain.model.Snooze

/** 활성 스누즈 영속화. 알람당 최대 하나이며, 서버 동기화 대상이 아니다. */
interface SnoozeRepository {
    suspend fun put(snooze: Snooze)

    suspend fun clear(alarmId: String)

    suspend fun getAll(): List<Snooze>
}
