package app.cozybuddy.hub.feature.settings

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.cozybuddy.hub.core.network.PairResult
import app.cozybuddy.hub.core.network.PairingClient
import app.cozybuddy.hub.core.network.PairingEndpoint
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.network.UrlError
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import timber.log.Timber

/** 코드 등록 실패 사유. 문구는 화면이 리소스에서 해석한다. */
sealed interface PairingError {
    data class Url(val error: UrlError) : PairingError

    data object CodeFormat : PairingError

    data object InvalidCode : PairingError

    data object RateLimited : PairingError

    data object Network : PairingError

    data object Server : PairingError

    data object Storage : PairingError
}

sealed interface PairingStatus {
    data object Idle : PairingStatus

    data object InProgress : PairingStatus

    /** 등록을 마쳤고 저장소가 바뀌어 연결이 다시 열린다. */
    data class Paired(val clientId: String) : PairingStatus

    data class Failed(val error: PairingError) : PairingStatus
}

data class PairingUiState(
    val url: String = "",
    val code: String = "",
    val status: PairingStatus = PairingStatus.Idle,
) {
    val inProgress: Boolean get() = status is PairingStatus.InProgress
}

/** 설정 화면의 "코드로 등록". 받은 자격 증명을 기존 암호화 설정 저장소에 넣어 재연결을 일으킨다. */
@HiltViewModel
class PairingViewModel @Inject constructor(
    private val settings: ServerSettings,
    private val client: PairingClient,
) : ViewModel() {

    private val _uiState = MutableStateFlow(PairingUiState())
    val uiState: StateFlow<PairingUiState> = _uiState

    init {
        viewModelScope.launch {
            val stored = settings.config.first()
            _uiState.update { if (it.url.isEmpty()) it.copy(url = stored.url) else it }
        }
    }

    fun onUrlChange(value: String) = _uiState.update { it.copy(url = value, status = PairingStatus.Idle) }

    fun onCodeChange(value: String) =
        _uiState.update { it.copy(code = value.filter(Char::isDigit).take(CODE_LENGTH), status = PairingStatus.Idle) }

    fun onPair() {
        val current = _uiState.value
        if (current.inProgress) return
        val endpoint = when (val resolved = PairingEndpoint.resolve(current.url)) {
            is PairingEndpoint.ResolveResult.Invalid -> return fail(PairingError.Url(resolved.error))
            is PairingEndpoint.ResolveResult.Valid -> resolved.endpoint
        }
        if (current.code.length != CODE_LENGTH) return fail(PairingError.CodeFormat)

        _uiState.update { it.copy(status = PairingStatus.InProgress) }
        viewModelScope.launch {
            when (val result = client.pair(endpoint, current.code)) {
                is PairResult.Success -> store(endpoint, result)
                PairResult.InvalidCode -> fail(PairingError.InvalidCode)
                PairResult.BadResponse -> fail(PairingError.Server)
                PairResult.RateLimited -> fail(PairingError.RateLimited)
                PairResult.NetworkError -> fail(PairingError.Network)
                PairResult.ServerError -> fail(PairingError.Server)
            }
        }
    }

    private suspend fun store(endpoint: PairingEndpoint, result: PairResult.Success) {
        val credentials = result.credentials
        try {
            settings.update(ServerConfig(endpoint.wsUrl, credentials.clientId, credentials.token))
            _uiState.update { it.copy(code = "", status = PairingStatus.Paired(credentials.clientId)) }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            Timber.e(e, "Failed to store paired credentials")
            fail(PairingError.Storage)
        }
    }

    private fun fail(error: PairingError) = _uiState.update { it.copy(status = PairingStatus.Failed(error)) }

    private companion object {
        const val CODE_LENGTH = 6
    }
}
