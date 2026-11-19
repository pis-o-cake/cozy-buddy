package app.cozybuddy.hub.core.network

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.jupiter.api.AfterEach
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** MockWebServer 로 페어링 HTTP 호출과 오류 분류를 검증한다. */
class PairingClientTest {

    private val server = MockWebServer()
    private val client = OkHttpPairingClient(OkHttpClient(), Dispatchers.IO)

    @AfterEach
    fun tearDown() = server.close()

    private fun endpoint(): PairingEndpoint {
        server.start()
        val base = server.url("/").toString().trimEnd('/')
        return PairingEndpoint("$base/api/auth/pair", "ws://unused/ws")
    }

    private fun respond(code: Int, body: String = "") =
        server.enqueue(MockResponse.Builder().code(code).body(body).build())

    @Test
    fun `success returns credentials and posts code and kind as json`() = runBlocking {
        respond(200, """{"client_id":"hub-ab12cd34","token":"tok_-Abc123"}""")

        val result = client.pair(endpoint(), "123456")

        assertEquals(PairResult.Success(PairedCredentials("hub-ab12cd34", "tok_-Abc123")), result)
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/auth/pair", request.url.encodedPath)
        assertTrue(request.headers["Content-Type"]!!.startsWith("application/json"))
        assertEquals("""{"code":"123456","client_kind":"hub"}""", request.body!!.utf8())
    }

    @Test
    fun `400 is an invalid or expired code`() = runBlocking {
        respond(400, """{"message_key":"auth.pairing_code_invalid"}""")
        assertEquals(PairResult.InvalidCode, client.pair(endpoint(), "000000"))
    }

    @Test
    fun `429 is rate limited`() = runBlocking {
        respond(429)
        assertEquals(PairResult.RateLimited, client.pair(endpoint(), "000000"))
    }

    @Test
    fun `5xx is a server error`() = runBlocking {
        respond(503)
        assertEquals(PairResult.ServerError, client.pair(endpoint(), "000000"))
    }

    @Test
    fun `success without credentials is a bad response`() = runBlocking {
        respond(200, """{"client_id":"hub-1"}""")
        assertEquals(PairResult.BadResponse, client.pair(endpoint(), "000000"))
    }

    @Test
    fun `unreachable server is a network error`() = runBlocking {
        val dead = endpoint()
        server.close()
        assertEquals(PairResult.NetworkError, client.pair(dead, "000000"))
    }

    @Test
    fun `credentials never print the token`() {
        assertFalse(PairedCredentials("hub-1", "secret-value").toString().contains("secret-value"))
    }
}
