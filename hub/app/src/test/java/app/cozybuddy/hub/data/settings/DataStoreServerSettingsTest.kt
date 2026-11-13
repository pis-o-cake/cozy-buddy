package app.cozybuddy.hub.data.settings

import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import app.cozybuddy.hub.core.network.ServerConfig
import java.io.File
import java.security.GeneralSecurityException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir

class DataStoreServerSettingsTest {

    @TempDir
    lateinit var dir: File

    private class ReverseCipher : TokenCipher {
        override fun encrypt(plain: String) = "enc:" + plain.reversed()
        override fun decrypt(encoded: String): String {
            if (!encoded.startsWith("enc:")) throw GeneralSecurityException("bad")
            return encoded.removePrefix("enc:").reversed()
        }
    }

    @Test
    fun `token is stored encrypted and read back in plain`() = runTest {
        val file = File(dir, "s.preferences_pb")
        val store = PreferenceDataStoreFactory.create(scope = backgroundScope) { file }
        val settings = DataStoreServerSettings(store, ReverseCipher())

        settings.update(ServerConfig(" ws://192.168.0.1/ws ", "hub-1", "plain-token"))

        assertEquals(ServerConfig("ws://192.168.0.1/ws", "hub-1", "plain-token"), settings.config.first())
        assertFalse(String(file.readBytes(), Charsets.ISO_8859_1).contains("plain-token"))
    }

    @Test
    fun `undecryptable token reads as empty`() = runTest {
        val store = PreferenceDataStoreFactory.create(scope = backgroundScope) { File(dir, "t.preferences_pb") }
        val writer = DataStoreServerSettings(store, ReverseCipher())
        writer.update(ServerConfig("ws://a.local/ws", "hub-1", "tok"))

        val reader = DataStoreServerSettings(store, object : TokenCipher {
            override fun encrypt(plain: String) = plain
            override fun decrypt(encoded: String): String = throw GeneralSecurityException("key gone")
        })

        assertEquals("", reader.config.first().token)
    }
}
