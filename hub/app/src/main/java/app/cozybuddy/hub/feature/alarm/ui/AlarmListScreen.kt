package app.cozybuddy.hub.feature.alarm.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.background
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TimeInput
import androidx.compose.material3.rememberTimePickerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.feature.alarm.AlarmDetail
import app.cozybuddy.hub.feature.alarm.AlarmItemUi
import app.cozybuddy.hub.feature.alarm.AlarmListUiState
import app.cozybuddy.hub.feature.alarm.AlarmListViewModel
import java.time.DayOfWeek
import timber.log.Timber

@Composable
fun AlarmListRoute(viewModel: AlarmListViewModel, onClose: () -> Unit, modifier: Modifier = Modifier) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current
    LifecycleResumeEffect(Unit) {
        viewModel.refreshExactAlarmState()
        onPauseOrDispose { }
    }
    AlarmListScreen(
        state = state,
        onClose = onClose,
        onToggle = viewModel::onToggle,
        onDelete = viewModel::onDelete,
        onAddAlarm = viewModel::onAddAlarm,
        onAddTimer = viewModel::onAddQuickTimer,
        onOpenExactAlarmSettings = { openExactAlarmSettings(context) },
        modifier = modifier,
    )
}

@Composable
fun AlarmListScreen(
    state: AlarmListUiState,
    onClose: () -> Unit,
    onToggle: (String, Boolean) -> Unit,
    onDelete: (String) -> Unit,
    onAddAlarm: (Int, Int, Set<DayOfWeek>) -> Unit,
    onAddTimer: () -> Unit,
    onOpenExactAlarmSettings: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var showDialog by rememberSaveable { mutableStateOf(false) }

    Column(
        modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = 48.dp, vertical = 24.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                stringResource(R.string.alarm_list_title),
                style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.onBackground,
                modifier = Modifier.weight(1f),
            )
            OutlinedButton(onClick = onAddTimer) { Text(stringResource(R.string.alarm_list_add_timer)) }
            Spacer(Modifier.width(12.dp))
            Button(onClick = { showDialog = true }) { Text(stringResource(R.string.alarm_list_add)) }
            Spacer(Modifier.width(12.dp))
            TextButton(onClick = onClose) { Text(stringResource(R.string.alarm_list_close)) }
        }
        if (!state.exactAlarmGranted) {
            ExactAlarmHint(onOpenExactAlarmSettings)
        }
        if (state.items.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    stringResource(R.string.alarm_list_empty),
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            LazyColumn(Modifier.fillMaxSize().padding(top = 16.dp)) {
                items(state.items, key = { it.id }) { item ->
                    AlarmRow(item, onToggle, onDelete)
                    HorizontalDivider(color = MaterialTheme.colorScheme.surfaceVariant)
                }
            }
        }
    }

    if (showDialog) {
        AddAlarmDialog(
            state = state,
            onDismiss = { showDialog = false },
            onConfirm = { hour, minute, days ->
                showDialog = false
                onAddAlarm(hour, minute, days)
            },
        )
    }
}

@Composable
private fun ExactAlarmHint(onOpenSettings: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(top = 12.dp)
            .background(MaterialTheme.colorScheme.surfaceVariant)
            .padding(horizontal = 16.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            stringResource(R.string.alarm_exact_permission_hint),
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onOpenSettings) { Text(stringResource(R.string.alarm_exact_permission_action)) }
    }
}

@Composable
private fun AlarmRow(item: AlarmItemUi, onToggle: (String, Boolean) -> Unit, onDelete: (String) -> Unit) {
    val title = item.label.ifBlank { defaultLabel(item.kind) }
    val toggleDescription = stringResource(R.string.alarm_list_toggle_description, title)
    Row(
        Modifier.fillMaxWidth().padding(vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Column(Modifier.weight(1f)) {
            Text(item.timeText, style = MaterialTheme.typography.headlineLarge, color = MaterialTheme.colorScheme.onBackground)
            Text(
                "$title · ${detailText(item.detail)}",
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        Switch(
            checked = item.enabled,
            onCheckedChange = { onToggle(item.id, it) },
            modifier = Modifier.semantics { contentDescription = toggleDescription },
        )
        Spacer(Modifier.width(16.dp))
        TextButton(onClick = { onDelete(item.id) }) { Text(stringResource(R.string.alarm_list_delete)) }
    }
}

@Composable
private fun detailText(detail: AlarmDetail): String = when (detail) {
    AlarmDetail.EveryDay -> stringResource(R.string.alarm_list_every_day)
    is AlarmDetail.Days -> detail.text
    is AlarmDetail.Date -> detail.text
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddAlarmDialog(
    state: AlarmListUiState,
    onDismiss: () -> Unit,
    onConfirm: (Int, Int, Set<DayOfWeek>) -> Unit,
) {
    val timeState = rememberTimePickerState(initialHour = DEFAULT_HOUR, initialMinute = 0, is24Hour = state.is24Hour)
    val selected = remember { mutableStateListOf<DayOfWeek>() }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text(stringResource(R.string.alarm_dialog_title)) },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(16.dp)) {
                // 분 단위 입력이 필요한 개발용 추가이므로 다이얼 대신 입력 필드형을 쓴다(가로 화면 높이에 맞다).
                TimeInput(state = timeState)
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    state.dayOptions.forEach { option ->
                        FilterChip(
                            selected = option.day in selected,
                            onClick = {
                                if (option.day in selected) selected.remove(option.day) else selected.add(option.day)
                            },
                            label = { Text(option.shortName) },
                        )
                    }
                }
                Text(
                    stringResource(R.string.alarm_dialog_repeat_hint),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
        confirmButton = {
            TextButton(onClick = { onConfirm(timeState.hour, timeState.minute, selected.toSet()) }) {
                Text(stringResource(R.string.alarm_dialog_confirm))
            }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text(stringResource(R.string.alarm_dialog_cancel)) }
        },
    )
}

private const val DEFAULT_HOUR = 7

// 정확 알람 권한은 API 31 이상에서만 설정 화면이 있다.
private fun openExactAlarmSettings(context: Context) {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return
    val intent = Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM, Uri.parse("package:${context.packageName}"))
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    try {
        context.startActivity(intent)
    } catch (e: ActivityNotFoundException) {
        Timber.w(e, "Exact alarm settings screen not available")
    }
}
