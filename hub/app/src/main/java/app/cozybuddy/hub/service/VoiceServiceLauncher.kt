package app.cozybuddy.hub.service

import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import timber.log.Timber

/**
 * 보이는 Activity 에서 마이크 권한을 받고 [HubForegroundService] 를 시작한다.
 *
 * 권한을 거부해도 서비스는 마이크 없이(mediaPlayback 만) 시작해 응답 재생과 상태 표시는 유지한다.
 * Activity 필드로 만들어 `onCreate` 이전에 등록되게 한다.
 */
class VoiceServiceLauncher(private val activity: ComponentActivity) {

    private val permission = activity.registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) Timber.w("RECORD_AUDIO denied; voice runs in degraded mode")
        HubForegroundService.start(activity)
    }

    fun launch() {
        val granted = ContextCompat.checkSelfPermission(activity, Manifest.permission.RECORD_AUDIO) ==
            PackageManager.PERMISSION_GRANTED
        if (granted) HubForegroundService.start(activity) else permission.launch(Manifest.permission.RECORD_AUDIO)
    }
}
