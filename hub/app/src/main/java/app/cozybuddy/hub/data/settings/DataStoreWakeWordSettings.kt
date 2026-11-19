package app.cozybuddy.hub.data.settings

import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.floatPreferencesKey
import androidx.datastore.preferences.core.stringPreferencesKey
import app.cozybuddy.hub.core.audio.wakeword.WakeSensitivity
import app.cozybuddy.hub.core.audio.wakeword.WakeWordKeyword
import app.cozybuddy.hub.core.audio.wakeword.WakeWordPrefs
import app.cozybuddy.hub.core.audio.wakeword.WakeWordSettings
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** 서버 설정과 같은 DataStore 파일에 키만 달리해 둔다. */
@Singleton
class DataStoreWakeWordSettings @Inject constructor(
    private val store: DataStore<Preferences>,
) : WakeWordSettings {

    override val prefs: Flow<WakeWordPrefs> = store.data.map { prefs ->
        WakeWordPrefs(
            enabled = prefs[KEY_ENABLED] ?: true,
            sensitivity = (prefs[KEY_SENSITIVITY] ?: WakeSensitivity.DEFAULT).coerceIn(0f, 1f),
            keyword = WakeWordKeyword.entries.firstOrNull { it.name == prefs[KEY_KEYWORD] } ?: WakeWordKeyword.HEY_JARVIS,
        )
    }

    override suspend fun setEnabled(enabled: Boolean) {
        store.edit { it[KEY_ENABLED] = enabled }
    }

    override suspend fun setSensitivity(sensitivity: Float) {
        store.edit { it[KEY_SENSITIVITY] = sensitivity.coerceIn(0f, 1f) }
    }

    override suspend fun setKeyword(keyword: WakeWordKeyword) {
        store.edit {
            it[KEY_KEYWORD] = keyword.name
            it[KEY_SENSITIVITY] = WakeSensitivity.DEFAULT
        }
    }

    private companion object {
        val KEY_ENABLED = booleanPreferencesKey("wake_word_enabled")
        val KEY_SENSITIVITY = floatPreferencesKey("wake_word_sensitivity")
        val KEY_KEYWORD = stringPreferencesKey("wake_word_keyword")
    }
}
