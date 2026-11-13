package app.cozybuddy.hub.core.network

import kotlinx.coroutines.flow.Flow

/** 서버 접속 설정 저장소. 값이 바뀌면 [config] 가 새 값을 내보내 연결이 다시 열린다. */
interface ServerSettings {
    val config: Flow<ServerConfig>

    suspend fun update(config: ServerConfig)
}
