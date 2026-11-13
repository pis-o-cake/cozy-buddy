package app.cozybuddy.hub.data.home

import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.entityCommand
import app.cozybuddy.protocol.v1.envelope
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withTimeoutOrNull
import timber.log.Timber

/** 기기 제어 실패 사유. 화면은 이 값을 문구 리소스로 옮긴다. */
enum class CommandFailure { UNAVAILABLE, UNSUPPORTED_ACTION, ENTITY_NOT_FOUND, NOT_CONNECTED, TIMEOUT, GENERIC }

sealed interface CommandOutcome {
    data object Success : CommandOutcome
    data class Failure(val reason: CommandFailure) : CommandOutcome
}

/** 기기 제어 명령을 서버로 보내고 결과를 기다린다. */
interface DeviceCommandSender {
    suspend fun send(entityId: String, action: String, params: Map<String, String> = emptyMap()): CommandOutcome
}

/** [HubLink] 로 EntityCommand 를 보내고 같은 request_id 의 CommandResult 로 완료한다. */
@Singleton
class LinkDeviceCommandSender @Inject constructor(
    private val link: HubLink,
) : DeviceCommandSender {

    override suspend fun send(entityId: String, action: String, params: Map<String, String>): CommandOutcome {
        val requestId = UUID.randomUUID().toString()
        val command = envelope {
            entityCommand = entityCommand {
                this.requestId = requestId
                this.entityId = entityId
                this.action = action
                this.params.putAll(params)
            }
        }
        return try {
            exchange(requestId, command)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Timber.w(e, "Command %s failed unexpectedly", requestId)
            CommandOutcome.Failure(CommandFailure.GENERIC)
        }
    }

    private suspend fun exchange(requestId: String, command: Envelope): CommandOutcome = coroutineScope {
        // IMPORTANT: 응답이 전송 직후 도착할 수 있으므로 구독을 먼저 건 뒤 보낸다.
        val reply = async(start = CoroutineStart.UNDISPATCHED) {
            withTimeoutOrNull(TIMEOUT_MS) {
                link.inbound.first { it.bodyCase == Envelope.BodyCase.COMMAND_RESULT && it.commandResult.requestId == requestId }
            }
        }
        if (!link.send(command)) {
            reply.cancel()
            Timber.w("Command %s not sent: link is not ready", requestId)
            return@coroutineScope CommandOutcome.Failure(CommandFailure.NOT_CONNECTED)
        }
        val result = reply.await()
        if (result == null) {
            Timber.w("Command %s timed out", requestId)
            return@coroutineScope CommandOutcome.Failure(CommandFailure.TIMEOUT)
        }
        val body = result.commandResult
        if (body.ok) return@coroutineScope CommandOutcome.Success
        Timber.w("Command %s rejected: code=%s key=%s", requestId, body.code, body.messageKey)
        CommandOutcome.Failure(failureOf(body.messageKey))
    }

    companion object {
        const val TIMEOUT_MS = 5_000L

        internal fun failureOf(messageKey: String): CommandFailure = when (messageKey) {
            "home.unavailable" -> CommandFailure.UNAVAILABLE
            "home.unsupported_action" -> CommandFailure.UNSUPPORTED_ACTION
            "home.entity_not_found" -> CommandFailure.ENTITY_NOT_FOUND
            else -> CommandFailure.GENERIC
        }
    }
}
