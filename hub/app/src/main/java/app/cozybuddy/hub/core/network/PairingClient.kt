package app.cozybuddy.hub.core.network

import java.io.IOException
import java.net.URI
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import timber.log.Timber

/** 페어링으로 받은 자격 증명. [token] 은 로그·예외 메시지로 새지 않도록 마스킹한다. */
data class PairedCredentials(val clientId: String, val token: String) {
    override fun toString(): String = "PairedCredentials(clientId=$clientId, token=***)"
}

/** 페어링 호출 결과. 화면은 종류별로 리소스 문구를 고른다. */
sealed interface PairResult {
    data class Success(val credentials: PairedCredentials) : PairResult

    /** 코드가 없거나 만료됐거나 이미 쓰였다. */
    data object InvalidCode : PairResult

    /** 같은 주소에서 시도가 너무 많다. */
    data object RateLimited : PairResult

    /** 서버에 닿지 못했다. */
    data object NetworkError : PairResult

    /** 서버가 오류 응답을 줬다. */
    data object ServerError : PairResult

    /** 성공 응답인데 자격 증명을 읽지 못했다. */
    data object BadResponse : PairResult
}

/** 서버에 6자리 코드를 보내 허브 ID 와 토큰을 받는 창구. */
interface PairingClient {
    suspend fun pair(endpoint: PairingEndpoint, code: String): PairResult
}

/**
 * 사용자가 입력한 서버 주소에서 페어링 REST 주소와 WebSocket 주소를 만든다.
 *
 * `http(s)://host:port` 와 `ws(s)://host:port/ws` 를 모두 받는다. 평문은 [ServerUrlValidator] 와 같은 규칙으로
 * 사설망·루프백에서만 허용하므로 공인 주소로 토큰이 평문 전송되지 않는다.
 */
data class PairingEndpoint(val pairUrl: String, val wsUrl: String) {

    companion object {
        private const val PAIR_PATH = "/api/auth/pair"
        private const val WS_PATH = "/ws"

        fun resolve(input: String): ResolveResult {
            val trimmed = input.trim()
            if (trimmed.isEmpty()) return ResolveResult.Invalid(UrlError.BLANK)
            val uri = runCatching { URI(trimmed) }.getOrNull() ?: return ResolveResult.Invalid(UrlError.MALFORMED)
            val authority = uri.rawAuthority?.takeIf { it.isNotBlank() && uri.host != null }
                ?: return ResolveResult.Invalid(UrlError.MALFORMED)
            val (httpScheme, wsScheme) = when (uri.scheme?.lowercase()) {
                "http", "ws" -> "http" to "ws"
                "https", "wss" -> "https" to "wss"
                else -> return ResolveResult.Invalid(UrlError.UNSUPPORTED_SCHEME)
            }
            val isWsInput = uri.scheme.lowercase().startsWith("ws")
            val wsUrl = if (isWsInput) trimmed else "$wsScheme://$authority$WS_PATH"
            ServerUrlValidator.validate(wsUrl)?.let { return ResolveResult.Invalid(it) }
            return ResolveResult.Valid(PairingEndpoint("$httpScheme://$authority$PAIR_PATH", wsUrl))
        }
    }

    sealed interface ResolveResult {
        data class Valid(val endpoint: PairingEndpoint) : ResolveResult

        data class Invalid(val error: UrlError) : ResolveResult
    }
}

class OkHttpPairingClient(
    client: OkHttpClient,
    private val ioDispatcher: CoroutineDispatcher,
) : PairingClient {

    @Inject
    constructor(client: OkHttpClient) : this(client, Dispatchers.IO)

    // 연결용 클라이언트는 읽기 타임아웃이 없으므로 짧은 REST 호출용으로 따로 제한한다.
    private val http = client.newBuilder()
        .callTimeout(CALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .readTimeout(CALL_TIMEOUT_SECONDS, TimeUnit.SECONDS)
        .build()

    override suspend fun pair(endpoint: PairingEndpoint, code: String): PairResult = withContext(ioDispatcher) {
        val body = """{"code":"$code","client_kind":"hub"}""".toRequestBody(JSON)
        val request = Request.Builder().url(endpoint.pairUrl).post(body).build()
        try {
            http.newCall(request).execute().use { response ->
                when {
                    response.isSuccessful -> parse(response.body.string())
                    response.code == HTTP_BAD_REQUEST -> PairResult.InvalidCode
                    response.code == HTTP_TOO_MANY_REQUESTS -> PairResult.RateLimited
                    else -> PairResult.ServerError
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: IOException) {
            Timber.w("Pairing request failed: %s", e.javaClass.simpleName)
            PairResult.NetworkError
        } catch (e: IllegalArgumentException) {
            Timber.w("Pairing URL rejected: %s", e.javaClass.simpleName)
            PairResult.NetworkError
        }
    }

    private fun parse(json: String): PairResult {
        val clientId = CLIENT_ID.find(json)?.groupValues?.get(1)
        val token = TOKEN.find(json)?.groupValues?.get(1)
        if (clientId.isNullOrBlank() || token.isNullOrBlank()) return PairResult.BadResponse
        return PairResult.Success(PairedCredentials(clientId, token))
    }

    private companion object {
        const val CALL_TIMEOUT_SECONDS = 10L
        const val HTTP_BAD_REQUEST = 400
        const val HTTP_TOO_MANY_REQUESTS = 429
        val JSON = "application/json".toMediaType()
        val CLIENT_ID = Regex(""""client_id"\s*:\s*"([^"\\]+)"""")
        val TOKEN = Regex(""""token"\s*:\s*"([^"\\]+)"""")
    }
}
