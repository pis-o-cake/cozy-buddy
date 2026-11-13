package app.cozybuddy.hub.core.network

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNull
import org.junit.jupiter.api.Test

class ServerUrlValidatorTest {

    @Test
    fun `private and loopback hosts may use cleartext ws`() {
        listOf(
            "ws://192.168.0.171:8120/ws",
            "ws://10.0.2.2:8120/ws",
            "ws://172.16.5.4/ws",
            "ws://localhost:8120/ws",
            "ws://cozy.local/ws",
            "ws://cozy-server:8120/ws",
        ).forEach { assertNull(ServerUrlValidator.validate(it), it) }
    }

    @Test
    fun `public hosts require wss`() {
        assertEquals(UrlError.CLEARTEXT_NOT_LOCAL, ServerUrlValidator.validate("ws://example.com/ws"))
        assertEquals(UrlError.CLEARTEXT_NOT_LOCAL, ServerUrlValidator.validate("ws://8.8.8.8/ws"))
        assertEquals(UrlError.CLEARTEXT_NOT_LOCAL, ServerUrlValidator.validate("ws://172.32.0.1/ws"))
        assertNull(ServerUrlValidator.validate("wss://example.com/ws"))
    }

    @Test
    fun `malformed and unsupported values are reported`() {
        assertEquals(UrlError.BLANK, ServerUrlValidator.validate("  "))
        assertEquals(UrlError.UNSUPPORTED_SCHEME, ServerUrlValidator.validate("http://192.168.0.1/ws"))
        assertEquals(UrlError.MALFORMED, ServerUrlValidator.validate("not a url"))
        assertEquals(UrlError.MALFORMED, ServerUrlValidator.validate("ws:///ws"))
    }

    @Test
    fun `config to string never prints the token`() {
        assertFalse(ServerConfig("ws://a", "id", "s3cret").toString().contains("s3cret"))
    }
}
