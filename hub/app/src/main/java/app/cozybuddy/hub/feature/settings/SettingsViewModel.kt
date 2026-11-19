package app.cozybuddy.hub.feature.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.core.audio.wakeword.WakeSensitivity
import app.cozybuddy.hub.core.audio.wakeword.WakeWordEngine
import app.cozybuddy.hub.core.audio.wakeword.WakeWordKeyword
import app.cozybuddy.hub.core.audio.wakeword.WakeWordSettings
import app.cozybuddy.hub.core.audio.wakeword.WakeWordStatus
import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.network.ServerUrlValidator
import app.cozybuddy.hub.core.network.UrlError
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import timber.log.Timber

/** 입력 검증 실패 항목. 문구는 화면이 리소스에서 해석한다. */
data class SettingsErrors(
    val url: UrlError? = null,
    val clientIdBlank: Boolean = false,
    val tokenBlank: Boolean = false,
) {
    val any: Boolean get() = url != null || clientIdBlank || tokenBlank
}

data class SettingsUiState(
    val url: String = "",
    val clientId: String = "",
    val token: String = "",
    val connection: ConnectionState = ConnectionState.Disconnected,
    val errors: SettingsErrors = SettingsErrors(),
    val saved: Boolean = false,
    val wakeWordEnabled: Boolean = true,
    val wakeWordSensitivity: Float = WakeSensitivity.DEFAULT,
    val wakeWordKeyword: WakeWordKeyword = WakeWordKeyword.HEY_JARVIS,
    val wakeWordStatus: WakeWordStatus = WakeWordStatus.Idle,
)

@HiltViewModel
class SettingsViewModel @Inject constructor(
    private val settings: ServerSettings,
    private val wakeWordSettings: WakeWordSettings,
    wakeWordEngine: WakeWordEngine,
    link: HubLink,
) : ViewModel() {

    private val form = MutableStateFlow(SettingsUiState())

    val uiState: StateFlow<SettingsUiState> = combine(form, link.state, wakeWordEngine.status) { form, connection, wakeStatus ->
        form.copy(connection = connection, wakeWordStatus = wakeStatus)
    }.stateIn(viewModelScope, SharingStarted.WhileSubscribed(STOP_TIMEOUT_MS), SettingsUiState())

    init {
        viewModelScope.launch {
            // 코드 등록처럼 다른 경로로 저장소가 바뀌어도 폼이 낡은 값으로 되돌려 저장하지 않게 계속 따라간다.
            settings.config.collect { stored ->
                form.update { it.copy(url = stored.url, clientId = stored.clientId, token = stored.token) }
            }
        }
        viewModelScope.launch {
            val prefs = wakeWordSettings.prefs.first()
            form.update { it.copy(wakeWordEnabled = prefs.enabled, wakeWordSensitivity = prefs.sensitivity, wakeWordKeyword = prefs.keyword) }
        }
    }

    fun onWakeWordEnabledChange(enabled: Boolean) {
        form.update { it.copy(wakeWordEnabled = enabled) }
        persist("wake word enabled") { wakeWordSettings.setEnabled(enabled) }
    }

    fun onWakeWordKeywordChange(keyword: WakeWordKeyword) {
        form.update { it.copy(wakeWordKeyword = keyword, wakeWordSensitivity = WakeSensitivity.DEFAULT) }
        persist("wake word keyword") { wakeWordSettings.setKeyword(keyword) }
    }

    /** 슬라이더를 끄는 동안의 값. 저장은 [onWakeWordSensitivityCommit] 에서 한 번만 한다. */
    fun onWakeWordSensitivityChange(value: Float) = form.update { it.copy(wakeWordSensitivity = value.coerceIn(0f, 1f)) }

    fun onWakeWordSensitivityCommit() {
        val value = form.value.wakeWordSensitivity
        persist("wake word sensitivity") { wakeWordSettings.setSensitivity(value) }
    }

    private fun persist(what: String, block: suspend () -> Unit) {
        viewModelScope.launch {
            try {
                block()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Timber.e(e, "Failed to save %s", what)
            }
        }
    }

    fun onUrlChange(value: String) = form.update { it.copy(url = value, saved = false) }

    fun onClientIdChange(value: String) = form.update { it.copy(clientId = value, saved = false) }

    fun onTokenChange(value: String) = form.update { it.copy(token = value, saved = false) }

    fun onSave() {
        val current = form.value
        val errors = SettingsErrors(
            url = ServerUrlValidator.validate(current.url),
            clientIdBlank = current.clientId.isBlank(),
            tokenBlank = current.token.isBlank(),
        )
        form.update { it.copy(errors = errors, saved = false) }
        if (errors.any) return
        viewModelScope.launch {
            try {
                settings.update(ServerConfig(current.url, current.clientId, current.token))
                form.update { it.copy(saved = true) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                Timber.e(e, "Failed to save server settings")
            }
        }
    }

    private companion object {
        const val STOP_TIMEOUT_MS = 5_000L
    }
}
