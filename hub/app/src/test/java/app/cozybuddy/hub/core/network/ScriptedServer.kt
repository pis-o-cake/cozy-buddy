package app.cozybuddy.hub.core.network

import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.ErrorCode
import app.cozybuddy.protocol.v1.envelope
import app.cozybuddy.protocol.v1.errorMessage
import app.cozybuddy.protocol.v1.hubConfig
import app.cozybuddy.protocol.v1.pong
import app.cozybuddy.protocol.v1.serverHello
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString

/** MockWebServer 위에서 Cozy Server 의 핸드셰이크 동작을 흉내 내는 테스트 서버. */
class ScriptedServer : AutoCloseable {

    enum class HelloMode { ACCEPT, REJECT_UNAUTHENTICATED, CLOSE_SILENTLY, IGNORE }

    private val server = MockWebServer()
    val received = Channel<Envelope>(Channel.UNLIMITED)
    val openedAtMs = CopyOnWriteArrayList<Long>()
    private val sockets = CopyOnWriteArrayList<WebSocket>()
    private val opened = AtomicInteger()

    @Volatile var helloMode = HelloMode.ACCEPT

    @Volatile var autoPong = true

    val connectionCount: Int get() = opened.get()
    val url: String get() = server.url("/ws").toString().replaceFirst("http", "ws")
    val lastSocket: WebSocket get() = sockets.last()

    fun start(upgrades: Int = 1) {
        server.start()
        repeat(upgrades) { server.enqueue(MockResponse.Builder().webSocketUpgrade(listener()).build()) }
    }

    fun push(body: Envelope) {
        lastSocket.send(body.toByteArray().toByteString())
    }

    suspend fun next(timeoutMs: Long = TIMEOUT_MS): Envelope = withTimeout(timeoutMs) { received.receive() }

    private fun listener() = object : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            sockets.add(webSocket)
            openedAtMs.add(System.currentTimeMillis())
            opened.incrementAndGet()
            if (helloMode == HelloMode.CLOSE_SILENTLY) webSocket.close(CLOSE_NORMAL, null)
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            val message = Envelope.parseFrom(bytes.toByteArray())
            received.trySend(message)
            when (message.bodyCase) {
                Envelope.BodyCase.CLIENT_HELLO -> answerHello(webSocket)
                Envelope.BodyCase.PING -> if (autoPong) {
                    webSocket.send(
                        envelope { pong = pong { pingSentEpochMs = message.ping.sentEpochMs } }
                            .toByteArray().toByteString(),
                    )
                }
                else -> Unit
            }
        }
    }

    private fun answerHello(webSocket: WebSocket) {
        val reply = when (helloMode) {
            HelloMode.ACCEPT -> envelope {
                serverHello = serverHello {
                    serverVersion = "test"
                    hubConfig = hubConfig {
                        name = "거실 허브"
                        areaId = "living"
                    }
                }
            }
            HelloMode.REJECT_UNAUTHENTICATED -> envelope {
                error = errorMessage {
                    code = ErrorCode.ERROR_CODE_UNAUTHENTICATED
                    messageKey = "error.unauthenticated"
                }
            }
            HelloMode.CLOSE_SILENTLY, HelloMode.IGNORE -> return
        }
        webSocket.send(reply.toByteArray().toByteString())
        if (helloMode == HelloMode.REJECT_UNAUTHENTICATED) webSocket.close(CLOSE_POLICY_VIOLATION, null)
    }

    override fun close() {
        sockets.forEach { runCatching { it.close(CLOSE_NORMAL, null) } }
        server.close()
    }

    companion object {
        const val TIMEOUT_MS = 5_000L
        private const val CLOSE_NORMAL = 1000
        private const val CLOSE_POLICY_VIOLATION = 1008
    }
}
