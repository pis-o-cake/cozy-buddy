package app.cozybuddy.hub.core.network

import java.net.URI

/** 서버 접속 설정. [token] 은 메모리에서만 평문이고 저장 시 암호화된다. */
data class ServerConfig(
    val url: String = "",
    val clientId: String = "",
    val token: String = "",
) {
    val isComplete: Boolean get() = url.isNotBlank() && clientId.isNotBlank() && token.isNotBlank()

    /** 토큰이 로그·예외 메시지로 새지 않도록 마스킹한다. */
    override fun toString(): String = "ServerConfig(url=$url, clientId=$clientId, token=***)"
}

enum class UrlError { BLANK, MALFORMED, UNSUPPORTED_SCHEME, CLEARTEXT_NOT_LOCAL }

/**
 * 서버 주소 검증.
 *
 * 평문 `ws://` 는 사설망·루프백 주소에서만 허용한다. 네트워크 보안 설정이 평문을 열어 두므로
 * 공인 주소로 토큰이 평문 전송되는 것을 코드에서 막는다.
 */
object ServerUrlValidator {

    fun validate(url: String): UrlError? {
        val trimmed = url.trim()
        if (trimmed.isEmpty()) return UrlError.BLANK
        val uri = runCatching { URI(trimmed) }.getOrNull() ?: return UrlError.MALFORMED
        val host = uri.host?.takeIf { it.isNotBlank() } ?: return UrlError.MALFORMED
        return when (uri.scheme?.lowercase()) {
            "wss" -> null
            "ws" -> if (isLocalHost(host)) null else UrlError.CLEARTEXT_NOT_LOCAL
            else -> UrlError.UNSUPPORTED_SCHEME
        }
    }

    /**
     * 평문 ws 를 허용할 호스트인지 판정한다.
     *
     * IPv4 는 점으로 나뉜 4개 라벨이 모두 0..255 숫자일 때만 주소로 본다.
     * `10.0.0.1.evil.com` 같은 공인 도메인은 숫자 접두어와 무관하게 비로컬이다.
     * 단일 라벨(`hub`, `nas`)은 공유기·mDNS 가 푸는 LAN 이름이라 허용한다. 공인 DNS 에는 단일 라벨이 없다.
     */
    internal fun isLocalHost(host: String): Boolean {
        val lower = host.lowercase()
        if (lower == "localhost" || lower.endsWith(".local")) return true
        if (!lower.contains('.') && !lower.contains(':')) return true
        val labels = lower.split('.')
        if (labels.size != IPV4_OCTETS || labels.any { !it.all { c -> c in '0'..'9' } || it.isEmpty() }) return false
        val octets = labels.map { it.toIntOrNull() ?: return false }
        if (octets.any { it !in 0..255 }) return false
        val (a, b) = octets
        return a == 10 || a == 127 || (a == 172 && b in 16..31) || (a == 192 && b == 168) || (a == 169 && b == 254)
    }

    private const val IPV4_OCTETS = 4
}
