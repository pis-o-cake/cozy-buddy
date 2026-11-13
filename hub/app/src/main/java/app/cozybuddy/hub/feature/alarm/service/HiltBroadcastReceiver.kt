package app.cozybuddy.hub.feature.alarm.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.annotation.CallSuper

/**
 * Hilt 가 바이트코드로 주입 코드를 끼우는 지점.
 *
 * Kotlin 은 추상 메서드인 BroadcastReceiver.onReceive 의 super 호출을 거부하므로, 구체 구현을 둔 중간 클래스를 쓴다.
 */
abstract class HiltBroadcastReceiver : BroadcastReceiver() {
    @CallSuper
    override fun onReceive(context: Context, intent: Intent) = Unit
}
