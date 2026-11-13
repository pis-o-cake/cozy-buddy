package app.cozybuddy.hub.di

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.PreferenceDataStoreFactory
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.preferencesDataStoreFile
import app.cozybuddy.hub.BuildConfig
import app.cozybuddy.hub.core.network.HubConnection
import app.cozybuddy.hub.core.network.HubLink
import app.cozybuddy.hub.core.network.ServerSettings
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.data.settings.AndroidKeystoreTokenCipher
import app.cozybuddy.hub.data.settings.DataStoreServerSettings
import app.cozybuddy.hub.data.settings.TokenCipher
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import okhttp3.OkHttpClient

@Module
@InstallIn(SingletonComponent::class)
abstract class NetworkBindings {

    @Binds
    abstract fun bindServerSettings(impl: DataStoreServerSettings): ServerSettings

    @Binds
    abstract fun bindTokenCipher(impl: AndroidKeystoreTokenCipher): TokenCipher

    @Binds
    abstract fun bindHubLink(impl: HubConnection): HubLink
}

@Module
@InstallIn(SingletonComponent::class)
object NetworkModule {

    @Provides
    @Singleton
    @ApplicationScope
    fun provideApplicationScope(): CoroutineScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    // 생존 확인은 프로토콜의 Ping/Pong 이 맡으므로 OkHttp 자체 ping 은 끄고 읽기 타임아웃도 두지 않는다.
    @Provides
    @Singleton
    fun provideOkHttpClient(): OkHttpClient = OkHttpClient.Builder()
        .pingInterval(java.time.Duration.ZERO)
        .readTimeout(java.time.Duration.ZERO)
        .build()

    @Provides
    @Singleton
    fun provideDataStore(@ApplicationContext context: Context): DataStore<Preferences> =
        PreferenceDataStoreFactory.create { context.preferencesDataStoreFile("server_settings") }

    @Provides
    @Singleton
    fun provideHubConnection(
        client: OkHttpClient,
        settings: ServerSettings,
        @ApplicationScope scope: CoroutineScope,
        timeSource: TimeSource,
    ): HubConnection = HubConnection(
        client = client,
        settings = settings.config,
        scope = scope,
        timeSource = timeSource,
        appVersion = BuildConfig.VERSION_NAME,
    )
}
