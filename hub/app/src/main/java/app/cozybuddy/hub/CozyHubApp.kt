package app.cozybuddy.hub

import android.app.Application
import dagger.hilt.android.HiltAndroidApp
import javax.inject.Inject
import timber.log.Timber

@HiltAndroidApp
class CozyHubApp : Application() {

    @Inject
    lateinit var runtime: HubRuntime

    override fun onCreate() {
        super.onCreate()
        // 릴리스 빌드에는 Tree 를 심지 않는다. 마이크·발화 로그가 기기에 남지 않게 하려는 의도다.
        if (BuildConfig.DEBUG) {
            Timber.plant(Timber.DebugTree())
        }
        runtime.start()
    }
}
