package app.cozybuddy.hub.domain.repository

import app.cozybuddy.hub.domain.model.Alarm
import kotlinx.coroutines.flow.Flow

interface AlarmRepository {
    fun observeAll(): Flow<List<Alarm>>

    suspend fun get(id: String): Alarm?

    /** 저장하고 허브가 부여한 revision 과 updatedAt 이 채워진 값을 돌려준다. */
    suspend fun upsert(alarm: Alarm): Alarm

    /** 삭제도 허브 revision 을 올린다. 없는 id 면 false 이고 revision 은 그대로다. */
    suspend fun delete(id: String): Boolean
}
