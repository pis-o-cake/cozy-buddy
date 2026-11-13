package app.cozybuddy.hub

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import app.cozybuddy.hub.core.designsystem.CozyTheme
import app.cozybuddy.hub.feature.alarm.AlarmListViewModel
import app.cozybuddy.hub.feature.alarm.AlarmRingViewModel
import app.cozybuddy.hub.feature.alarm.ui.AlarmListRoute
import app.cozybuddy.hub.feature.alarm.ui.AlarmRingOverlayRoute
import app.cozybuddy.hub.feature.ambient.AmbientViewModel
import app.cozybuddy.hub.feature.ambient.ui.AmbientRoute
import app.cozybuddy.hub.feature.dashboard.DashboardViewModel
import app.cozybuddy.hub.feature.dashboard.ui.DashboardRoute
import app.cozybuddy.hub.feature.settings.SettingsViewModel
import app.cozybuddy.hub.feature.settings.ui.SettingsRoute
import dagger.hilt.android.AndroidEntryPoint

/** 허브의 유일한 Activity. HOME 앱이므로 부팅 직후 보이는 상태로 시작한다. */
@AndroidEntryPoint
class MainActivity : ComponentActivity() {

    private val ambientViewModel: AmbientViewModel by viewModels()
    private val alarmListViewModel: AlarmListViewModel by viewModels()
    private val alarmRingViewModel: AlarmRingViewModel by viewModels()
    private val settingsViewModel: SettingsViewModel by viewModels()
    private val dashboardViewModel: DashboardViewModel by viewModels()

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        requestNotificationPermissionIfNeeded()
        setContent {
            CozyTheme {
                HubRoot()
            }
        }
    }

    @Composable
    private fun HubRoot() {
        var screen by rememberSaveable { mutableStateOf(HubScreen.AMBIENT) }
        BackHandler(enabled = screen == HubScreen.DASHBOARD) { screen = HubScreen.AMBIENT }
        BackHandler(enabled = screen == HubScreen.ALARMS) { screen = HubScreen.AMBIENT }
        BackHandler(enabled = screen == HubScreen.SETTINGS) { screen = HubScreen.ALARMS }
        Box {
            when (screen) {
                HubScreen.AMBIENT -> AmbientRoute(
                    ambientViewModel,
                    onLongPress = { screen = HubScreen.ALARMS },
                    onSwipeLeft = { screen = HubScreen.DASHBOARD },
                )
                HubScreen.DASHBOARD -> DashboardRoute(dashboardViewModel, onClose = { screen = HubScreen.AMBIENT })
                HubScreen.ALARMS -> AlarmListRoute(
                    alarmListViewModel,
                    onClose = { screen = HubScreen.AMBIENT },
                    onOpenSettings = { screen = HubScreen.SETTINGS },
                )
                HubScreen.SETTINGS -> SettingsRoute(settingsViewModel, onClose = { screen = HubScreen.ALARMS })
            }
            // 울림 오버레이는 어떤 화면 위에서도 보여야 하므로 마지막에 그린다.
            AlarmRingOverlayRoute(alarmRingViewModel, Modifier)
        }
    }

    // Android 13 이상은 알림 권한이 꺼지면 울림 알림·전체 화면 인텐트가 표시되지 않는다.
    private fun requestNotificationPermissionIfNeeded() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        val granted = ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
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

private enum class HubScreen { AMBIENT, DASHBOARD, ALARMS, SETTINGS }
