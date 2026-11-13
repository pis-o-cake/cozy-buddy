package app.cozybuddy.hub.data.settings

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import app.cozybuddy.hub.core.network.ServerConfig
import app.cozybuddy.hub.core.network.ServerSettings
import java.security.GeneralSecurityException
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import timber.log.Timber

/** URL·클라이언트 ID 는 평문, 토큰은 [TokenCipher] 로 암호화해 DataStore 에 둔다. */
@Singleton
class DataStoreServerSettings @Inject constructor(
    private val store: DataStore<Preferences>,
    private val cipher: TokenCipher,
) : ServerSettings {

    override val config: Flow<ServerConfig> = store.data.map { prefs ->
        ServerConfig(
            url = prefs[KEY_URL].orEmpty(),
            clientId = prefs[KEY_CLIENT_ID].orEmpty(),
            token = decryptToken(prefs[KEY_TOKEN]),
        )
    }

    override suspend fun update(config: ServerConfig) {
        val encrypted = if (config.token.isEmpty()) "" else cipher.encrypt(config.token)
        store.edit { prefs ->
            prefs[KEY_URL] = config.url.trim()
            prefs[KEY_CLIENT_ID] = config.clientId.trim()
            prefs[KEY_TOKEN] = encrypted
        }
    }

    // 키가 무효화된 기기에서는 빈 토큰으로 취급해 사용자가 다시 입력하게 한다.
    private fun decryptToken(encoded: String?): String {
        if (encoded.isNullOrEmpty()) return ""
        return try {
            cipher.decrypt(encoded)
        } catch (e: GeneralSecurityException) {
            Timber.w(e, "Stored token could not be decrypted")
            ""
        } catch (e: IllegalArgumentException) {
            Timber.w(e, "Stored token is malformed")
            ""
        }
    }

    private companion object {
        val KEY_URL = stringPreferencesKey("server_url")
        val KEY_CLIENT_ID = stringPreferencesKey("client_id")
        val KEY_TOKEN = stringPreferencesKey("token_encrypted")
    }
}
