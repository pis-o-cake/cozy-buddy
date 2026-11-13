package app.cozybuddy.hub.di

import app.cozybuddy.hub.core.audio.AudioCapture
import app.cozybuddy.hub.core.audio.AudioRecordCapture
import app.cozybuddy.hub.core.audio.AudioTrackSink
import app.cozybuddy.hub.core.audio.PcmSinkFactory
import app.cozybuddy.hub.core.audio.ResponsePlayer
import app.cozybuddy.hub.core.audio.SileroVad
import app.cozybuddy.hub.core.audio.StreamingResponsePlayer
import app.cozybuddy.hub.core.audio.Vad
import app.cozybuddy.hub.core.audio.wakeword.AssetWakeWordModelStore
import app.cozybuddy.hub.core.audio.wakeword.OnnxWakeWordModelFactory
import app.cozybuddy.hub.core.audio.wakeword.OpenWakeWordEngine
import app.cozybuddy.hub.core.audio.wakeword.WakeWordConfig
import app.cozybuddy.hub.core.audio.wakeword.WakeWordEngine
import app.cozybuddy.hub.core.audio.wakeword.WakeWordModelFactory
import app.cozybuddy.hub.core.audio.wakeword.WakeWordModelStore
import app.cozybuddy.hub.core.audio.wakeword.WakeWordSettings
import app.cozybuddy.hub.data.settings.DataStoreWakeWordSettings
import app.cozybuddy.hub.domain.voice.AlarmRingingSource
import app.cozybuddy.hub.domain.voice.StreamIdGenerator
import app.cozybuddy.hub.domain.voice.VoiceClientConfig
import app.cozybuddy.hub.feature.alarm.RingingState
import app.cozybuddy.hub.feature.alarm.RingingStateHolder
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import java.util.UUID
import java.util.concurrent.Executors
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.stateIn

@Module
@InstallIn(SingletonComponent::class)
abstract class VoiceBindings {

    @Binds
    abstract fun bindAudioCapture(impl: AudioRecordCapture): AudioCapture

    @Binds
    abstract fun bindVad(impl: SileroVad): Vad

    @Binds
    abstract fun bindWakeWordEngine(impl: OpenWakeWordEngine): WakeWordEngine

    @Binds
    abstract fun bindWakeWordModelStore(impl: AssetWakeWordModelStore): WakeWordModelStore

    @Binds
    abstract fun bindWakeWordModelFactory(impl: OnnxWakeWordModelFactory): WakeWordModelFactory

    @Binds
    abstract fun bindWakeWordSettings(impl: DataStoreWakeWordSettings): WakeWordSettings
}

@Module
@InstallIn(SingletonComponent::class)
object VoiceModule {

    @Provides
    @Singleton
    fun provideResponsePlayer(): ResponsePlayer {
        // AudioTrack.write 는 블로킹이므로 플레이어 전용 스레드를 둔다.
        val dispatcher = Executors.newSingleThreadExecutor { Thread(it, "response-player") }.asCoroutineDispatcher()
        return StreamingResponsePlayer(PcmSinkFactory { AudioTrackSink(it) }, dispatcher)
    }

    @Provides
    @Singleton
    fun provideAlarmRingingSource(holder: RingingStateHolder, @ApplicationScope scope: CoroutineScope): AlarmRingingSource {
        val ringing = holder.state
            .map { it is RingingState.Ringing }
            .stateIn(scope, SharingStarted.Eagerly, false)
        return object : AlarmRingingSource {
            override val ringing: StateFlow<Boolean> = ringing
        }
    }

    @Provides
    fun provideStreamIdGenerator(): StreamIdGenerator = StreamIdGenerator { UUID.randomUUID().toString() }

    @Provides
    fun provideWakeWordConfig(): WakeWordConfig = WakeWordConfig()

    @Provides
    fun provideVoiceClientConfig(): VoiceClientConfig = VoiceClientConfig()
}
