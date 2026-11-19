package app.cozybuddy.hub.core.network

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class PairingEndpointTest {

    private fun valid(input: String) =
        (PairingEndpoint.resolve(input) as PairingEndpoint.ResolveResult.Valid).endpoint

    private fun invalid(input: String) =
        (PairingEndpoint.resolve(input) as PairingEndpoint.ResolveResult.Invalid).error

    @Test
    fun `http base maps to rest and ws urls`() {
        val e = valid("http://192.168.0.10:8120")
        assertEquals("http://192.168.0.10:8120/api/auth/pair", e.pairUrl)
        assertEquals("ws://192.168.0.10:8120/ws", e.wsUrl)
    }

    @Test
    fun `ws url keeps its path and maps to http host`() {
        val e = valid("ws://192.168.0.10:8120/ws")
        assertEquals("http://192.168.0.10:8120/api/auth/pair", e.pairUrl)
        assertEquals("ws://192.168.0.10:8120/ws", e.wsUrl)
    }

    @Test
    fun `secure schemes stay secure for public hosts`() {
        val e = valid("https://cozy.example.com")
        assertEquals("https://cozy.example.com/api/auth/pair", e.pairUrl)
        assertEquals("wss://cozy.example.com/ws", e.wsUrl)
    }

    @Test
    fun `cleartext to a public host is rejected`() {
        assertEquals(UrlError.CLEARTEXT_NOT_LOCAL, invalid("http://cozy.example.com"))
    }

    @Test
    fun `blank malformed and unknown schemes are rejected`() {
        assertEquals(UrlError.BLANK, invalid("  "))
        assertEquals(UrlError.MALFORMED, invalid("http://"))
        assertEquals(UrlError.UNSUPPORTED_SCHEME, invalid("ftp://192.168.0.1"))
    }
}
