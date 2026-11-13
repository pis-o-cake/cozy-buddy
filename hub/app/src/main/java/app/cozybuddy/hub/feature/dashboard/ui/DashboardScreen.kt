package app.cozybuddy.hub.feature.dashboard.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.GridItemSpan
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Slider
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.data.home.CommandFailure
import app.cozybuddy.hub.feature.dashboard.AreaSection
import app.cozybuddy.hub.feature.dashboard.CardKind
import app.cozybuddy.hub.feature.dashboard.CardState
import app.cozybuddy.hub.feature.dashboard.DashboardConnection
import app.cozybuddy.hub.feature.dashboard.DashboardUiState
import app.cozybuddy.hub.feature.dashboard.DashboardViewModel
import app.cozybuddy.hub.feature.dashboard.EntityCard

private const val SWIPE_CLOSE_THRESHOLD_PX = 160f
private const val ERROR_VISIBLE_MS = 3_000L
private const val DISABLED_ALPHA = 0.45f
private val CARD_MIN_WIDTH = 240.dp
private val CARD_MIN_HEIGHT = 132.dp

@Composable
fun DashboardRoute(viewModel: DashboardViewModel, onClose: () -> Unit) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()

    DisposableEffect(viewModel) {
        viewModel.onScreenShown()
        onDispose { viewModel.onScreenHidden() }
    }
    LaunchedEffect(viewModel) { viewModel.returnToAmbient.collect { onClose() } }

    DashboardScreen(
        state = state,
        onPrimaryAction = viewModel::onPrimaryAction,
        onBrightnessChange = viewModel::onBrightnessChange,
        onErrorShown = viewModel::onErrorShown,
        onUserInteraction = viewModel::onUserInteraction,
        onClose = onClose,
    )
}

@Composable
fun DashboardScreen(
    state: DashboardUiState,
    onPrimaryAction: (String) -> Unit,
    onBrightnessChange: (String, Int) -> Unit,
    onErrorShown: () -> Unit,
    onUserInteraction: () -> Unit,
    onClose: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(
        modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .pointerInput(onUserInteraction) {
                // 자식이 이벤트를 소비하기 전(Initial)에 보고 소비하지 않으므로 다른 제스처를 막지 않는다.
                awaitPointerEventScope {
                    while (true) {
                        awaitPointerEvent(PointerEventPass.Initial)
                        onUserInteraction()
                    }
                }
            }
            .pointerInput(onClose) {
                var total = 0f
                detectHorizontalDragGestures(
                    onDragStart = { total = 0f },
                    onDragEnd = { if (total > SWIPE_CLOSE_THRESHOLD_PX) onClose() },
                    onDragCancel = { total = 0f },
                ) { _, delta -> total += delta }
            },
    ) {
        Column(Modifier.fillMaxSize()) {
            Header(onClose)
            ConnectionBanner(state.connection)
            if (state.sections.isEmpty()) {
                EmptyMessage()
            } else {
                DeviceGrid(state, onPrimaryAction, onBrightnessChange)
            }
        }
        state.error?.let { ErrorToast(it, onErrorShown, Modifier.align(Alignment.BottomCenter)) }
    }
}

@Composable
private fun Header(onClose: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 24.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = stringResource(R.string.dashboard_title),
            style = MaterialTheme.typography.headlineMedium,
            color = MaterialTheme.colorScheme.onBackground,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onClose, modifier = Modifier.heightIn(min = 56.dp)) {
            Text(stringResource(R.string.dashboard_close), style = MaterialTheme.typography.titleMedium)
        }
    }
}

@Composable
private fun ConnectionBanner(connection: DashboardConnection) {
    val text = when (connection) {
        DashboardConnection.CONNECTED -> return
        DashboardConnection.OFFLINE -> stringResource(R.string.dashboard_banner_offline)
        DashboardConnection.AUTH_FAILED -> stringResource(R.string.dashboard_banner_auth_failed)
    }
    Text(
        text = text,
        style = MaterialTheme.typography.bodyLarge,
        color = MaterialTheme.colorScheme.onPrimary,
        modifier = Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.primary)
            .padding(horizontal = 24.dp, vertical = 12.dp),
    )
}

@Composable
private fun EmptyMessage() {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(
            text = stringResource(R.string.dashboard_empty),
            style = MaterialTheme.typography.titleMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun DeviceGrid(
    state: DashboardUiState,
    onPrimaryAction: (String) -> Unit,
    onBrightnessChange: (String, Int) -> Unit,
) {
    LazyVerticalGrid(
        columns = GridCells.Adaptive(CARD_MIN_WIDTH),
        contentPadding = PaddingValues(horizontal = 24.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        state.sections.forEach { section ->
            item(key = "header:${section.areaId}", span = { GridItemSpan(maxLineSpan) }) {
                SectionTitle(section)
            }
            items(section.cards, key = { it.id }) { card ->
                DeviceCard(card, state.controlsEnabled, onPrimaryAction, onBrightnessChange)
            }
        }
    }
}

@Composable
private fun SectionTitle(section: AreaSection) {
    Text(
        text = section.areaId?.let { section.name } ?: stringResource(R.string.dashboard_area_other),
        style = MaterialTheme.typography.titleLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 12.dp),
    )
}

@Composable
private fun DeviceCard(
    card: EntityCard,
    controlsEnabled: Boolean,
    onPrimaryAction: (String) -> Unit,
    onBrightnessChange: (String, Int) -> Unit,
) {
    val actionable = card.kind != CardKind.READ_ONLY
    val enabled = controlsEnabled && card.state != CardState.UNAVAILABLE && !card.pending
    val active = card.state == CardState.ON || card.state == CardState.OPEN
    val container = if (active) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surface
    val content = if (active) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface

    Column(
        Modifier
            .fillMaxWidth()
            .heightIn(min = CARD_MIN_HEIGHT)
            .alpha(if (controlsEnabled || !actionable) 1f else DISABLED_ALPHA)
            .background(container, RoundedCornerShape(20.dp))
            .let {
                if (!actionable) it else it.clickable(enabled = enabled, role = Role.Button) { onPrimaryAction(card.id) }
            }
            .padding(20.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(
            text = card.name,
            style = MaterialTheme.typography.titleMedium,
            color = content,
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
        )
        Text(
            text = cardStatusText(card),
            style = MaterialTheme.typography.headlineSmall,
            color = content.copy(alpha = 0.8f),
        )
        val brightness = card.brightnessPct
        if (card.kind == CardKind.LIGHT && brightness != null) {
            BrightnessSlider(card, brightness, enabled = controlsEnabled && !card.pending, onBrightnessChange, content)
        }
        if (card.pending) {
            LinearProgressIndicator(Modifier.fillMaxWidth(), color = content, trackColor = content.copy(alpha = 0.2f))
        }
    }
}

@Composable
private fun BrightnessSlider(
    card: EntityCard,
    brightness: Int,
    enabled: Boolean,
    onBrightnessChange: (String, Int) -> Unit,
    contentColor: androidx.compose.ui.graphics.Color,
) {
    // 드래그 중에는 로컬 값을 보여주고, 서버가 확정한 밝기가 바뀌면 그 값으로 맞춘다.
    var value by remember(card.id, brightness) { mutableFloatStateOf(brightness.toFloat()) }
    val description = stringResource(R.string.dashboard_brightness_description, card.name)
    Slider(
        value = value,
        onValueChange = {
            value = it
            onBrightnessChange(card.id, it.toInt())
        },
        valueRange = 0f..100f,
        enabled = enabled,
        modifier = Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .semantics { contentDescription = description },
    )
    Text(
        text = stringResource(R.string.dashboard_brightness_value, value.toInt()),
        style = MaterialTheme.typography.bodyMedium,
        color = contentColor.copy(alpha = 0.8f),
    )
}

@Composable
private fun cardStatusText(card: EntityCard): String = when (card.state) {
    CardState.ON -> stringResource(R.string.dashboard_state_on)
    CardState.OFF -> when (card.kind) {
        CardKind.SCENE -> stringResource(R.string.dashboard_action_activate)
        CardKind.SCRIPT -> stringResource(R.string.dashboard_action_run)
        else -> stringResource(R.string.dashboard_state_off)
    }
    CardState.OPEN -> stringResource(R.string.dashboard_state_open)
    CardState.CLOSED -> stringResource(R.string.dashboard_state_closed)
    CardState.LOCKED -> stringResource(R.string.dashboard_state_locked)
    CardState.UNLOCKED -> stringResource(R.string.dashboard_state_unlocked)
    CardState.UNAVAILABLE -> stringResource(R.string.dashboard_state_unavailable)
    CardState.VALUE -> if (card.unit.isBlank()) card.value else stringResource(R.string.dashboard_state_value_unit, card.value, card.unit)
}

@Composable
private fun ErrorToast(failure: CommandFailure, onShown: () -> Unit, modifier: Modifier = Modifier) {
    val message = stringResource(
        when (failure) {
            CommandFailure.UNAVAILABLE -> R.string.dashboard_error_unavailable
            CommandFailure.UNSUPPORTED_ACTION -> R.string.dashboard_error_unsupported_action
            CommandFailure.ENTITY_NOT_FOUND -> R.string.dashboard_error_entity_not_found
            CommandFailure.NOT_CONNECTED -> R.string.dashboard_error_not_connected
            CommandFailure.TIMEOUT -> R.string.dashboard_error_timeout
            CommandFailure.GENERIC -> R.string.dashboard_error_generic
        },
    )
    LaunchedEffect(failure) {
        kotlinx.coroutines.delay(ERROR_VISIBLE_MS)
        onShown()
    }
    Text(
        text = message,
        style = MaterialTheme.typography.titleMedium,
        color = MaterialTheme.colorScheme.onSurface,
        modifier = modifier
            .padding(24.dp)
            .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(16.dp))
            .padding(horizontal = 24.dp, vertical = 16.dp),
    )
}
