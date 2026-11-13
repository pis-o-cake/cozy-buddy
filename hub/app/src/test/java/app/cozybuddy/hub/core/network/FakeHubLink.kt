package app.cozybuddy.hub.core.network

import app.cozybuddy.protocol.v1.Envelope
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow

/** 연결 계층 없이 수신·송신을 직접 다루는 테스트용 [HubLink]. */
class FakeHubLink : HubLink {
    override val state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    private val _inbound = MutableSharedFlow<Envelope>(extraBufferCapacity = 64)
    override val inbound: SharedFlow<Envelope> = _inbound
    val sent = mutableListOf<Envelope>()
    var accepting = true

    override fun send(envelope: Envelope): Boolean {
        if (!accepting) return false
        sent += envelope
        return true
    }

    suspend fun receive(envelope: Envelope) = _inbound.emit(envelope)

    fun connect() {
        state.value = ConnectionState.Connected(HubInfo("거실 허브", "living"))
    }
}
