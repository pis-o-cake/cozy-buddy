package app.cozybuddy.hub.feature.conversation.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.domain.voice.BroadcastSubtitle
import app.cozybuddy.hub.feature.conversation.ConversationViewModel

/**
 * 다른 허브가 보낸 방송 자막을 화면 위쪽에 띄운다. 터치를 가로채지 않는다.
 *
 * 대화 오버레이와 알람 울림 오버레이 위에 보여야 하므로 가장 마지막에 그린다. 대화 중이거나
 * 알람이 울려 방송 음성이 재생되지 않을 때도 자막은 남는다.
 */
@Composable
fun BroadcastBannerRoute(viewModel: ConversationViewModel, modifier: Modifier = Modifier) {
    val subtitle by viewModel.broadcast.collectAsStateWithLifecycle()
    BroadcastBanner(subtitle, modifier)
}

@Composable
internal fun BroadcastBanner(subtitle: BroadcastSubtitle?, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.TopCenter) {
        AnimatedVisibility(visible = subtitle != null, enter = fadeIn(), exit = fadeOut()) {
            val shown = subtitle ?: return@AnimatedVisibility
            Surface(
                modifier = Modifier.padding(horizontal = 64.dp, vertical = 24.dp),
                shape = RoundedCornerShape(24.dp),
                color = MaterialTheme.colorScheme.surfaceVariant,
                tonalElevation = 6.dp,
            ) {
                Column(Modifier.padding(horizontal = 32.dp, vertical = 20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
                    Text(
                        text = bannerTitle(shown),
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.primary,
                        textAlign = TextAlign.Center,
                    )
                    Text(
                        text = shown.text,
                        style = MaterialTheme.typography.headlineSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        textAlign = TextAlign.Center,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun bannerTitle(subtitle: BroadcastSubtitle): String =
    if (subtitle.fromArea.isEmpty()) {
        stringResource(R.string.broadcast_title)
    } else {
        stringResource(R.string.broadcast_title_from_area, subtitle.fromArea)
    }
