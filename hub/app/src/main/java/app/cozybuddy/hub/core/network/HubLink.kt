package app.cozybuddy.hub.core.network

import app.cozybuddy.protocol.v1.Envelope
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow

/** 서버 연결의 소비자용 창구. 기능 코드는 구현이 아니라 이 인터페이스에 의존한다. */
interface HubLink {
    val state: StateFlow<ConnectionState>

    /** 연결 중 받은 메시지. Ping/Pong 은 연결 계층이 처리하므로 나오지 않는다. 구독자가 없으면 버려진다. */
    val inbound: SharedFlow<Envelope>

    /** ServerHello 를 받은 연결에서만 보낼 수 있다. seq 는 연결이 매기며, 보내지 못하면 false. */
    fun send(envelope: Envelope): Boolean
}
