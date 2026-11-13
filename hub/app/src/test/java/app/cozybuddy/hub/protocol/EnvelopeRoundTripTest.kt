package app.cozybuddy.hub.protocol

import app.cozybuddy.protocol.v1.ClientKind
import app.cozybuddy.protocol.v1.Envelope
import app.cozybuddy.protocol.v1.clientHello
import app.cozybuddy.protocol.v1.envelope
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Test

class EnvelopeRoundTripTest {

    @Test
    fun `ClientHello envelope survives serialize and parse`() {
        val original = envelope {
            seq = 1
            clientHello = clientHello {
                kind = ClientKind.CLIENT_KIND_HUB
                clientId = "hub-living-room"
                token = "secret"
                appVersion = "0.1.0"
                protocolVersion = 1
            }
        }

        val parsed = Envelope.parseFrom(original.toByteArray())

        assertEquals(original, parsed)
        assertEquals(Envelope.BodyCase.CLIENT_HELLO, parsed.bodyCase)
        assertEquals(ClientKind.CLIENT_KIND_HUB, parsed.clientHello.kind)
        assertEquals("hub-living-room", parsed.clientHello.clientId)
    }
}
