package app.cozybuddy.hub.domain.voice

import app.cozybuddy.hub.core.audio.AudioCapture
import app.cozybuddy.hub.core.audio.PlaybackDrained
import app.cozybuddy.hub.core.audio.PlaybackFormat
import app.cozybuddy.hub.core.audio.ResponsePlayer
import app.cozybuddy.hub.core.audio.Vad
import app.cozybuddy.hub.core.audio.VadConfig
import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.di.ApplicationScope
import app.cozybuddy.protocol.v1.AudioChunk
import app.cozybuddy.protocol.v1.AudioEncoding
import app.cozybuddy.protocol.v1.CancelReason
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.ResponseAudioStart
import app.cozybuddy.protocol.v1.StreamKind
import app.cozybuddy.protocol.v1.WakeSource
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharedFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asSharedFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import timber.log.Timber

/** 화면에 잠깐 알려야 하는 사건. 상태 기계 전이와 별개다. */
enum class VoiceNotice { OFFLINE, MIC_UNAVAILABLE, ERROR }

/** 오버레이에 보일 자막. 발화마다 비워진다. */
data class ConversationText(
    val transcript: String = "",
    val transcriptFinal: Boolean = false,
    val response: String = "",
    val responseFinal: Boolean = false,
)

/** 울림 상태를 도메인이 아는 만큼만 노출한다. */
interface AlarmRingingSource {
    val ringing: StateFlow<Boolean>
}

fun interface StreamIdGenerator {
    fun next(): String
}

data class VoiceClientConfig(
    val uplink: UplinkConfig = UplinkConfig(),
    /** 호출어로 시작할 때의 pre-roll. 호출어 꼬리와 이어지는 첫 마디까지 덮어야 해서 탭(300 ms)보다 길다. */
    val wakeWordPreRollMs: Int = 1_000,
    val vad: VadConfig = VadConfig(),
    /** UtteranceEnd 이후 응답이 시작되지 않고 이만큼 지나면 오류로 끝낸다. */
    val thinkingTimeoutMs: Long = 30_000L,
    /** 응답 오디오가 시작된 뒤 End 없이 이만큼 지나면 멈춘 것으로 본다. */
    val speakingTimeoutMs: Long = 180_000L,
    /** 텍스트 응답이 끝났는데 오디오가 오지 않을 때 기다리는 시간. 텍스트만 오는 응답을 위한 것이다. */
    val textOnlyGraceMs: Long = 3_000L,
)

/**
 * 탭 투 토크 음성 클라이언트의 오케스트레이터. 상태 기계, 업링크, 응답 재생, 서버 수신 메시지를 하나로 묶는다.
 *
 * IMPORTANT: 모든 사건(탭, 수신 메시지, 연결 변화, 알람, 업링크 종료, 재생 소진)은 하나의 채널로 직렬화되어
 * 단일 코루틴에서 처리된다. 사건 간 경쟁을 락 없이 배제하려는 구조다.
 *
 * 수명은 포그라운드 서비스가 [start]/[stop] 으로 관리한다. 서비스가 없으면 탭은 [VoiceNotice.MIC_UNAVAILABLE] 이 된다.
 */
@Singleton
class VoiceClient @Inject constructor(
    private val link: HubLink,
    private val capture: AudioCapture,
    private val vad: Vad,
    private val player: ResponsePlayer,
    private val machine: AssistantStateMachine,
    private val alarm: AlarmRingingSource,
    private val ids: StreamIdGenerator,
    private val config: VoiceClientConfig,
    @ApplicationScope private val scope: CoroutineScope,
) {
    val state: StateFlow<AssistantSnapshot> = machine.state

    private val _conversation = MutableStateFlow(ConversationText())
    val conversation: StateFlow<ConversationText> = _conversation.asStateFlow()

    private val _micLevel = MutableStateFlow(0f)

    /** 듣는 중 마이크 레벨(0..1). 파형 표시용. */
    val micLevel: StateFlow<Float> = _micLevel.asStateFlow()

    private val _notices = MutableSharedFlow<VoiceNotice>(extraBufferCapacity = NOTICE_BUFFER)
    val notices: SharedFlow<VoiceNotice> = _notices.asSharedFlow()

    private sealed interface Command {
        data object Tap : Command
        data object WakeWord : Command
        data class Inbound(val envelope: Envelope) : Command
        data object LinkLost : Command
        data class Ringing(val ringing: Boolean) : Command
        data class UplinkFinished(val utteranceId: String, val result: UplinkResult) : Command
        data class Drained(val event: PlaybackDrained) : Command
        data class ThinkingTimeout(val utteranceId: String) : Command
    }

    private val commands = Channel<Command>(Channel.UNLIMITED)
    private var runner: Job? = null
    private var uplinkJob: Job? = null
    private var timeoutJob: Job? = null
    private var broadcastId: String? = null
    private val staleStreams = ArrayDeque<String>()

    private val uplink = UtteranceUplink(link, capture, vad, config.uplink, config.vad)

    fun start() {
        if (runner?.isActive == true) return
        runner = scope.launch {
            launch { link.inbound.collect { commands.trySend(Command.Inbound(it)) } }
            launch {
                link.state.collect {
                    if (it !is ConnectionState.Connected) commands.trySend(Command.LinkLost)
                }
            }
            launch { alarm.ringing.collect { commands.trySend(Command.Ringing(it)) } }
            launch { player.drained.collect { commands.trySend(Command.Drained(it)) } }
            for (command in commands) handle(command)
        }
        Timber.i("Voice client started")
    }

    fun stop() {
        runner?.cancel()
        runner = null
        releaseActivity()
        broadcastId = null
        machine.onDisconnected()
        Timber.i("Voice client stopped")
    }

    /** 마이크 버튼·오버레이 탭. 상태에 따라 시작, 끼어들기, 무시 중 하나가 된다. */
    fun onTap() {
        commands.trySend(Command.Tap)
    }

    /** 호출어 엔진이 감지했을 때. 상태에 따라 시작, 끼어들기, 무시 중 하나가 된다. */
    fun onWakeWord() {
        commands.trySend(Command.WakeWord)
    }

    private fun handle(command: Command) {
        when (command) {
            Command.Tap -> handleTap()
            Command.WakeWord -> handleWakeWord()
            is Command.Inbound -> handleInbound(command.envelope)
            Command.LinkLost -> handleLinkLost()
            is Command.Ringing -> handleRinging(command.ringing)
            is Command.UplinkFinished -> handleUplinkFinished(command.utteranceId, command.result)
            is Command.Drained -> handleDrained(command.event)
            is Command.ThinkingTimeout -> handleThinkingTimeout(command.utteranceId)
        }
    }

    private fun handleTap() {
        if (!machine.state.value.alarmRinging) {
            if (!capture.running.value) return notify(VoiceNotice.MIC_UNAVAILABLE)
            if (link.state.value !is ConnectionState.Connected) return notify(VoiceNotice.OFFLINE)
        }
        val connected = link.state.value as? ConnectionState.Connected
        val newId = ids.next()
        when (val result = machine.onTap(newId)) {
            is TapResult.Ignored -> Timber.d("Tap ignored: %s", result.reason)
            TapResult.Started -> beginUtterance(newId, requireNotNull(connected).hubInfo.areaId)
            is TapResult.BargeIn -> bargeIn(result.supersededUtteranceId, newId, requireNotNull(connected).hubInfo.areaId)
        }
    }

    private fun handleWakeWord() {
        if (!capture.running.value) return
        val connected = link.state.value as? ConnectionState.Connected
        if (connected == null) return Timber.d("Wake word ignored: offline")
        val newId = ids.next()
        val source = WakeSource.WAKE_SOURCE_WAKE_WORD
        when (val result = machine.onWakeWord(newId)) {
            is TapResult.Ignored -> Timber.d("Wake word ignored: %s", result.reason)
            TapResult.Started -> beginUtterance(newId, connected.hubInfo.areaId, source, config.wakeWordPreRollMs)
            is TapResult.BargeIn -> bargeIn(result.supersededUtteranceId, newId, connected.hubInfo.areaId, source, config.wakeWordPreRollMs)
        }
    }

    private fun bargeIn(
        supersededId: String,
        newId: String,
        areaId: String,
        source: WakeSource = config.uplink.source,
        preRollMs: Int = config.uplink.preRollMs,
    ) {
        releaseActivity()
        markStale(supersededId)
        link.send(VoiceMessages.utteranceCancel(supersededId, CancelReason.CANCEL_REASON_BARGE_IN))
        beginUtterance(newId, areaId, source, preRollMs)
    }

    private fun beginUtterance(
        utteranceId: String,
        areaId: String,
        source: WakeSource = config.uplink.source,
        preRollMs: Int = config.uplink.preRollMs,
    ) {
        stopBroadcast()
        _conversation.value = ConversationText()
        uplinkJob = scope.launch {
            try {
                val result = uplink.run(
                    streamId = utteranceId,
                    areaId = areaId,
                    canSend = { isListening(utteranceId) },
                    onLevel = { _micLevel.value = it },
                    source = source,
                    preRollMs = preRollMs,
                )
                commands.trySend(Command.UplinkFinished(utteranceId, result))
            } finally {
                _micLevel.value = 0f
            }
        }
    }

    private fun handleUplinkFinished(utteranceId: String, result: UplinkResult) {
        if (!isListening(utteranceId)) return
        when (result) {
            UplinkResult.Completed -> {
                machine.onUtteranceEnded(utteranceId)
                scheduleThinkingTimeout(utteranceId, config.thinkingTimeoutMs)
            }
            UplinkResult.NoSpeech -> {
                link.send(VoiceMessages.utteranceCancel(utteranceId, CancelReason.CANCEL_REASON_TIMEOUT))
                machine.onNoSpeech(utteranceId)
                markStale(utteranceId)
            }
            UplinkResult.Aborted -> Unit
            UplinkResult.LinkFailed, UplinkResult.CaptureFailed -> {
                link.send(VoiceMessages.utteranceCancel(utteranceId, CancelReason.CANCEL_REASON_USER))
                failCurrent(utteranceId)
            }
        }
    }

    private fun handleInbound(envelope: Envelope) {
        when (envelope.bodyCase) {
            Envelope.BodyCase.TRANSCRIPT -> {
                val message = envelope.transcript
                if (message.streamId != machine.state.value.utteranceId) return
                _conversation.value = _conversation.value.copy(transcript = message.text, transcriptFinal = message.final)
            }
            Envelope.BodyCase.RESPONSE_TEXT -> handleResponseText(envelope.responseText.streamId, envelope.responseText.delta, envelope.responseText.final)
            Envelope.BodyCase.RESPONSE_AUDIO_START -> handleAudioStart(envelope.responseAudioStart)
            Envelope.BodyCase.AUDIO_CHUNK -> handleAudioChunk(envelope.audioChunk)
            Envelope.BodyCase.RESPONSE_AUDIO_END -> handleAudioEnd(envelope.responseAudioEnd.streamId)
            Envelope.BodyCase.ASSISTANT_STATE ->
                Timber.d("Server assistant state: %s", envelope.assistantState.state)
            Envelope.BodyCase.ERROR -> handleServerError(envelope.error.streamId)
            else -> Unit
        }
    }

    private fun handleResponseText(streamId: String, delta: String, final: Boolean) {
        val phase = machine.state.value.phase
        val receiving = phase == AssistantPhase.THINKING || phase == AssistantPhase.SPEAKING
        if (!receiving || streamId in staleStreams) return
        _conversation.value = _conversation.value.let {
            it.copy(response = it.response + delta, responseFinal = final)
        }
        val utteranceId = machine.state.value.utteranceId
        if (final && phase == AssistantPhase.THINKING && utteranceId != null) {
            scheduleThinkingTimeout(utteranceId, config.textOnlyGraceMs)
        }
    }

    private fun handleAudioStart(message: ResponseAudioStart) {
        val format = message.format
        val supported = format.encoding == AudioEncoding.AUDIO_ENCODING_PCM_S16LE && format.channels <= 1
        if (!supported) return Timber.w("Unsupported response audio format: %s", format)
        val rate = format.sampleRateHz.takeIf { it > 0 } ?: PlaybackFormat.DEFAULT_SAMPLE_RATE_HZ
        when (message.kind) {
            StreamKind.STREAM_KIND_RESPONSE_DOWN -> {
                if (message.streamId in staleStreams) return
                if (!machine.onResponseAudioStart(message.streamId)) {
                    return Timber.d("Response audio ignored in phase %s", machine.state.value.phase)
                }
                machine.state.value.utteranceId?.let { scheduleThinkingTimeout(it, config.speakingTimeoutMs) }
                player.start(message.streamId, StreamKind.STREAM_KIND_RESPONSE_DOWN, rate)
            }
            StreamKind.STREAM_KIND_BROADCAST_DOWN -> {
                val busy = machine.state.value.let { it.phase != AssistantPhase.IDLE || it.alarmRinging }
                if (busy) return Timber.d("Broadcast skipped: assistant busy")
                broadcastId = message.streamId
                player.start(message.streamId, StreamKind.STREAM_KIND_BROADCAST_DOWN, rate)
            }
            else -> Timber.w("Unexpected response audio kind: %s", message.kind)
        }
    }

    private fun handleAudioChunk(chunk: AudioChunk) {
        val owned = when (chunk.kind) {
            StreamKind.STREAM_KIND_RESPONSE_DOWN -> chunk.streamId == machine.state.value.responseId
            StreamKind.STREAM_KIND_BROADCAST_DOWN -> chunk.streamId == broadcastId
            else -> false
        }
        if (!owned) return
        player.write(chunk.streamId, chunk.index, chunk.data.toByteArray())
        if (chunk.last) player.finish(chunk.streamId)
    }

    private fun handleAudioEnd(streamId: String) {
        val known = streamId == machine.state.value.responseId || streamId == broadcastId
        if (known) player.finish(streamId)
    }

    private fun handleServerError(streamId: String) {
        val state = machine.state.value
        if (state.phase == AssistantPhase.IDLE) return
        val relevant = streamId.isEmpty() || streamId == state.utteranceId || streamId == state.responseId
        if (!relevant) return
        state.utteranceId?.let { failCurrent(it) }
    }

    private fun handleDrained(event: PlaybackDrained) {
        if (event.kind == StreamKind.STREAM_KIND_BROADCAST_DOWN) {
            if (event.streamId == broadcastId) broadcastId = null
            return
        }
        val before = machine.state.value
        if (machine.onPlaybackDrained(event.streamId)) {
            markStale(event.streamId)
            before.utteranceId?.let(::markStale)
        }
    }

    private fun handleThinkingTimeout(utteranceId: String) {
        val state = machine.state.value
        val waiting = state.phase == AssistantPhase.THINKING || state.phase == AssistantPhase.SPEAKING
        if (!waiting || state.utteranceId != utteranceId) return
        Timber.w("Response timed out in phase %s", state.phase)
        link.send(VoiceMessages.utteranceCancel(utteranceId, CancelReason.CANCEL_REASON_TIMEOUT))
        failCurrent(utteranceId)
    }

    private fun handleLinkLost() {
        val wasActive = machine.state.value.phase != AssistantPhase.IDLE
        // 연결 상태는 Connecting 과 Disconnected 사이를 오가므로, 멈출 것이 있을 때만 정리한다.
        if (wasActive || broadcastId != null) releaseActivity()
        broadcastId = null
        machine.onDisconnected()
        if (wasActive) notify(VoiceNotice.OFFLINE)
    }

    private fun handleRinging(ringing: Boolean) {
        if (!ringing) return machine.onAlarmRinging(false)
        val before = machine.state.value
        releaseActivity()
        broadcastId = null
        before.utteranceId?.let {
            link.send(VoiceMessages.utteranceCancel(it, CancelReason.CANCEL_REASON_USER))
            markStale(it)
        }
        before.responseId?.let(::markStale)
        machine.onAlarmRinging(true)
    }

    private fun failCurrent(utteranceId: String) {
        markStale(utteranceId)
        machine.state.value.responseId?.let(::markStale)
        releaseActivity()
        machine.onError()
        notify(VoiceNotice.ERROR)
    }

    private fun isListening(utteranceId: String): Boolean =
        machine.state.value.let { it.phase == AssistantPhase.LISTENING && it.utteranceId == utteranceId }

    private fun scheduleThinkingTimeout(utteranceId: String, delayMs: Long) {
        timeoutJob?.cancel()
        timeoutJob = scope.launch {
            delay(delayMs)
            commands.trySend(Command.ThinkingTimeout(utteranceId))
        }
    }

    /** 진행 중인 업링크·타이머·재생을 모두 끊는다. 재생 중단은 즉시 소리를 멈춘다. */
    private fun releaseActivity() {
        uplinkJob?.cancel()
        uplinkJob = null
        timeoutJob?.cancel()
        timeoutJob = null
        player.stop()
        _micLevel.value = 0f
    }

    private fun stopBroadcast() {
        if (broadcastId == null) return
        player.stop()
        broadcastId = null
    }

    private fun markStale(streamId: String) {
        if (streamId in staleStreams) return
        staleStreams.addLast(streamId)
        if (staleStreams.size > STALE_LIMIT) staleStreams.removeFirst()
    }

    private fun notify(notice: VoiceNotice) {
        _notices.tryEmit(notice)
    }

    private companion object {
        const val NOTICE_BUFFER = 4
        const val STALE_LIMIT = 16
    }
}
