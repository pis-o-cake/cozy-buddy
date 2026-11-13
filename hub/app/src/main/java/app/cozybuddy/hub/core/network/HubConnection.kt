package app.cozybuddy.hub.core.network

import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.protocol.v1.ClientKind
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.ErrorCode
import app.cozybuddy.protocol.v1.clientHello
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.ping
import app.cozybuddy.protocol.v1.pong
import com.google.protobuf.InvalidProtocolBufferException
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import timber.log.Timber

/** 연결 계층 튜닝 값. 기본값이 운영 값이고 테스트만 줄여 쓴다. */
data class HubConnectionConfig(
    val pingIntervalMs: Long = 25_000L,
    /** 응답 없는 Ping 이 이만큼 쌓이면 연결이 죽은 것으로 본다. */
    val maxMissedPongs: Int = 2,
    val protocolVersion: Int = 1,
)

/**
 * 서버 WebSocket 연결. 바이너리 [Envelope] 프레임만 주고받는다.
 *
 * 앱 프로세스 범위 [scope] 에서 돌며 Activity 수명과 무관하다. 설정이 바뀌면 현재 연결을 닫고 새 설정으로 다시 연다.
 * 인증 실패(UNAUTHENTICATED)면 설정이 바뀔 때까지 재시도하지 않는다.
 */
class HubConnection(
    private val client: OkHttpClient,
    private val settings: Flow<ServerConfig>,
    private val scope: CoroutineScope,
    private val timeSource: TimeSource,
    private val appVersion: String,
    private val backoff: BackoffPolicy = BackoffPolicy(),
    private val config: HubConnectionConfig = HubConnectionConfig(),
) : HubLink {

    private val _state = MutableStateFlow<ConnectionState>(ConnectionState.Disconnected)
    override val state: StateFlow<ConnectionState> = _state.asStateFlow()

    private val _inbound = MutableSharedFlow<Envelope>(extraBufferCapacity = INBOUND_BUFFER)
    override val inbound: SharedFlow<Envelope> = _inbound.asSharedFlow()

    private val sendLock = Any()
    private var active: ActiveSocket? = null
    private var job: Job? = null

    private class ActiveSocket(val socket: WebSocket) {
        val seq = AtomicLong(0)
        var ready = false
    }

    private sealed interface SocketEvent {
        data object Opened : SocketEvent
        data class Frame(val bytes: ByteString) : SocketEvent
        data object Tick : SocketEvent
        data class Ended(val reason: String) : SocketEvent
    }

    private enum class SessionEnd { AUTH_FAILED, LOST }

    /** 연결 루프를 시작한다. 여러 번 호출해도 하나만 돈다. */
    @Synchronized
    fun start() {
        if (job?.isActive == true) return
        job = scope.launch {
            settings.distinctUntilChanged().collectLatest { cfg -> serve(cfg) }
        }
    }

    @Synchronized
    fun stop() {
        job?.cancel()
        job = null
        _state.value = ConnectionState.Disconnected
    }

    override fun send(envelope: Envelope): Boolean = synchronized(sendLock) {
        val current = active?.takeIf { it.ready } ?: return false
        transmit(current, envelope)
    }

    private suspend fun serve(cfg: ServerConfig) {
        if (!cfg.isComplete || ServerUrlValidator.validate(cfg.url) != null) {
            _state.value = ConnectionState.Disconnected
            return
        }
        var attempt = 0
        while (true) {
            _state.value = ConnectionState.Connecting
            val (end, reachedHello) = runSession(cfg)
            if (end == SessionEnd.AUTH_FAILED) {
                _state.value = ConnectionState.AuthFailed
                return
            }
            _state.value = ConnectionState.Disconnected
            if (reachedHello) attempt = 0
            val wait = backoff.delayMs(attempt++)
            Timber.i("Reconnecting in %d ms", wait)
            delay(wait)
        }
    }

    private suspend fun runSession(cfg: ServerConfig): Pair<SessionEnd, Boolean> {
        val events = Channel<SocketEvent>(Channel.UNLIMITED)
        val request = Request.Builder().url(cfg.url.trim()).build()
        val socket = client.newWebSocket(request, listener(events))
        val session = ActiveSocket(socket)
        var reachedHello = false
        try {
            return coroutineScope {
                val ticker = launch {
                    while (true) {
                        delay(config.pingIntervalMs)
                        events.trySend(SocketEvent.Tick)
                    }
                }
                val end = processEvents(cfg, session, events) { reachedHello = true }
                ticker.cancel()
                end to reachedHello
            }
        } finally {
            withContext(NonCancellable) {
                synchronized(sendLock) {
                    session.ready = false
                    if (active === session) active = null
                }
                socket.cancel()
                events.close()
            }
        }
    }

    private suspend fun processEvents(
        cfg: ServerConfig,
        session: ActiveSocket,
        events: Channel<SocketEvent>,
        onHello: () -> Unit,
    ): SessionEnd {
        var outstandingPings = 0
        var helloAccepted = false
        for (event in events) {
            when (event) {
                SocketEvent.Opened -> sendHello(session, cfg)
                SocketEvent.Tick -> {
                    if (!helloAccepted || outstandingPings >= config.maxMissedPongs) {
                        Timber.w("Connection considered dead (hello=%b, missedPongs=%d)", helloAccepted, outstandingPings)
                        return SessionEnd.LOST
                    }
                    outstandingPings++
                    transmitLocked(session, envelope { ping = ping { sentEpochMs = nowMs() } })
                }
                is SocketEvent.Ended -> {
                    Timber.i("Socket ended: %s", event.reason)
                    return SessionEnd.LOST
                }
                is SocketEvent.Frame -> {
                    val message = decode(event.bytes) ?: continue
                    when (message.bodyCase) {
                        Envelope.BodyCase.PONG -> outstandingPings = 0
                        Envelope.BodyCase.PING -> transmitLocked(
                            session,
                            envelope { pong = pong { pingSentEpochMs = message.ping.sentEpochMs } },
                        )
                        Envelope.BodyCase.SERVER_HELLO -> {
                            helloAccepted = true
                            onHello()
                            val hub = message.serverHello.hubConfig
                            synchronized(sendLock) {
                                session.ready = true
                                active = session
                            }
                            _state.value = ConnectionState.Connected(HubInfo(hub.name, hub.areaId))
                            _inbound.emit(message)
                        }
                        Envelope.BodyCase.ERROR -> {
                            Timber.w("Server error code=%s key=%s", message.error.code, message.error.messageKey)
                            if (message.error.code == ErrorCode.ERROR_CODE_UNAUTHENTICATED) return SessionEnd.AUTH_FAILED
                            _inbound.emit(message)
                        }
                        else -> _inbound.emit(message)
                    }
                }
            }
        }
        return SessionEnd.LOST
    }

    private fun sendHello(session: ActiveSocket, cfg: ServerConfig) {
        val hello = envelope {
            clientHello = clientHello {
                kind = ClientKind.CLIENT_KIND_HUB
                clientId = cfg.clientId
                token = cfg.token
                appVersion = this@HubConnection.appVersion
                protocolVersion = config.protocolVersion
            }
        }
        transmitLocked(session, hello)
    }

    private fun transmitLocked(session: ActiveSocket, envelope: Envelope) {
        synchronized(sendLock) { transmit(session, envelope) }
    }

    // sendLock 안에서만 호출한다. seq 부여와 전송 순서가 같아야 서버가 역전을 오탐하지 않는다.
    private fun transmit(session: ActiveSocket, envelope: Envelope): Boolean {
        val numbered = envelope.toBuilder().setSeq(session.seq.incrementAndGet()).build()
        return session.socket.send(numbered.toByteArray().toByteString())
    }

    private fun decode(bytes: ByteString): Envelope? = try {
        Envelope.parseFrom(bytes.toByteArray())
    } catch (e: InvalidProtocolBufferException) {
        Timber.w(e, "Dropped undecodable frame")
        null
    }

    private fun nowMs(): Long = timeSource.now().toInstant().toEpochMilli()

    private fun listener(events: Channel<SocketEvent>) = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            events.trySend(SocketEvent.Opened)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            events.trySend(SocketEvent.Frame(bytes))
        }

        // 텍스트 프레임은 프로토콜 위반이므로 무시하고 기록만 한다.
        override fun onMessage(webSocket: WebSocket, text: String) {
            Timber.w("Ignored text frame (%d chars)", text.length)
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(NORMAL_CLOSE, null)
            events.trySend(SocketEvent.Ended("closing $code"))
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            events.trySend(SocketEvent.Ended("closed $code"))
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            events.trySend(SocketEvent.Ended("failure ${t.javaClass.simpleName}"))
        }
    }

    private companion object {
        const val INBOUND_BUFFER = 64
        const val NORMAL_CLOSE = 1000
    }
}
