package app.cozybuddy.hub.core.network

/** 서버가 허브에 내려준 설정(ServerHello.hub_config). */
data class HubInfo(val name: String, val areaId: String)

/** 서버 연결 상태. 화면은 이 값만 보고 오프라인 표시를 결정한다. */
sealed interface ConnectionState {
    /** 연결 없음. 설정이 비어 있거나 재연결 대기 중이다. */
    data object Disconnected : ConnectionState

    /** 소켓을 열었거나 ServerHello 를 기다리는 중. */
    data object Connecting : ConnectionState

    data class Connected(val hubInfo: HubInfo) : ConnectionState

    /** 서버가 자격 증명을 거부했다. 설정이 바뀔 때까지 재시도하지 않는다. */
    data object AuthFailed : ConnectionState
}
