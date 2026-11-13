package app.cozybuddy.hub

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import app.cozybuddy.hub.core.designsystem.CozyTheme
import app.cozybuddy.hub.feature.ambient.AmbientViewModel
import app.cozybuddy.hub.feature.ambient.ui.AmbientRoute
import dagger.hilt.android.AndroidEntryPoint

/** 허브의 유일한 Activity. HOME 앱이므로 부팅 직후 보이는 상태로 시작한다. */
@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    private val ambientViewModel: AmbientViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContent {
            CozyTheme {
                AmbientRoute(ambientViewModel)
            }
        }
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) hideSystemBars()
    }

    // 스와이프로 일시 노출된 시스템 바는 시간이 지나면 다시 숨겨져야 하므로 포커스를 얻을 때마다 적용한다.
    private fun hideSystemBars() {
        val controller = WindowCompat.getInsetsController(window, window.decorView)
        controller.systemBarsBehavior =
            WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        controller.hide(WindowInsetsCompat.Type.systemBars())
    }
}
