package app.cozybuddy.hub.feature.settings.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Slider
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.core.audio.wakeword.WakeWordFailure
import app.cozybuddy.hub.core.audio.wakeword.WakeWordStatus
import app.cozybuddy.hub.core.network.ConnectionState
import app.cozybuddy.hub.core.network.UrlError
import app.cozybuddy.hub.feature.settings.SettingsUiState
import app.cozybuddy.hub.feature.settings.SettingsViewModel

@Composable
fun SettingsRoute(viewModel: SettingsViewModel, onClose: () -> Unit, modifier: Modifier = Modifier) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    SettingsScreen(
        state = state,
        onClose = onClose,
        onUrlChange = viewModel::onUrlChange,
        onClientIdChange = viewModel::onClientIdChange,
        onTokenChange = viewModel::onTokenChange,
        onSave = viewModel::onSave,
        onWakeWordEnabledChange = viewModel::onWakeWordEnabledChange,
        onWakeWordSensitivityChange = viewModel::onWakeWordSensitivityChange,
        onWakeWordSensitivityCommit = viewModel::onWakeWordSensitivityCommit,
        modifier = modifier,
    )
}

@Composable
fun SettingsScreen(
    state: SettingsUiState,
    onClose: () -> Unit,
    onUrlChange: (String) -> Unit,
    onClientIdChange: (String) -> Unit,
    onTokenChange: (String) -> Unit,
    onSave: () -> Unit,
    onWakeWordEnabledChange: (Boolean) -> Unit,
    onWakeWordSensitivityChange: (Float) -> Unit,
    onWakeWordSensitivityCommit: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background)
            .padding(horizontal = 48.dp, vertical = 24.dp)
            .verticalScroll(rememberScrollState()),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Text(
                stringResource(R.string.settings_title),
                style = MaterialTheme.typography.headlineMedium,
                color = MaterialTheme.colorScheme.onBackground,
                modifier = Modifier.weight(1f),
            )
            TextButton(onClick = onClose) { Text(stringResource(R.string.settings_close)) }
        }
        OutlinedTextField(
            value = state.url,
            onValueChange = onUrlChange,
            label = { Text(stringResource(R.string.settings_url_label)) },
            placeholder = { Text(stringResource(R.string.settings_url_hint)) },
            isError = state.errors.url != null,
            supportingText = state.errors.url?.let { { Text(stringResource(it.messageRes())) } },
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.clientId,
            onValueChange = onClientIdChange,
            label = { Text(stringResource(R.string.settings_client_id_label)) },
            isError = state.errors.clientIdBlank,
            supportingText = if (state.errors.clientIdBlank) {
                { Text(stringResource(R.string.settings_error_client_id)) }
            } else {
                null
            },
            singleLine = true,
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.token,
            onValueChange = onTokenChange,
            label = { Text(stringResource(R.string.settings_token_label)) },
            isError = state.errors.tokenBlank,
            supportingText = if (state.errors.tokenBlank) {
                { Text(stringResource(R.string.settings_error_token)) }
            } else {
                null
            },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(16.dp)) {
            Button(onClick = onSave) { Text(stringResource(R.string.settings_save)) }
            if (state.saved) {
                Text(stringResource(R.string.settings_saved), color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Text(
            "${stringResource(R.string.settings_status_label)}: ${connectionText(state.connection)}",
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        WakeWordSection(
            state = state,
            onEnabledChange = onWakeWordEnabledChange,
            onSensitivityChange = onWakeWordSensitivityChange,
            onSensitivityCommit = onWakeWordSensitivityCommit,
        )
    }
}

@Composable
private fun WakeWordSection(
    state: SettingsUiState,
    onEnabledChange: (Boolean) -> Unit,
    onSensitivityChange: (Float) -> Unit,
    onSensitivityCommit: () -> Unit,
) {
    Text(
        stringResource(R.string.settings_wake_word_title),
        style = MaterialTheme.typography.titleLarge,
        color = MaterialTheme.colorScheme.onBackground,
        modifier = Modifier.padding(top = 16.dp),
    )
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Text(
            stringResource(R.string.settings_wake_word_toggle),
            color = MaterialTheme.colorScheme.onBackground,
            modifier = Modifier.weight(1f),
        )
        Switch(checked = state.wakeWordEnabled, onCheckedChange = onEnabledChange)
    }
    Text(stringResource(R.string.settings_wake_word_sensitivity), color = MaterialTheme.colorScheme.onSurfaceVariant)
    Slider(
        value = state.wakeWordSensitivity,
        onValueChange = onSensitivityChange,
        onValueChangeFinished = onSensitivityCommit,
        enabled = state.wakeWordEnabled,
    )
    Text(
        stringResource(R.string.settings_wake_word_sensitivity_hint),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    val failure = (state.wakeWordStatus as? WakeWordStatus.Failed)?.failure
    if (state.wakeWordEnabled && failure != null) {
        Text(stringResource(failure.messageRes()), color = MaterialTheme.colorScheme.error)
    }
}

private fun WakeWordFailure.messageRes(): Int = when (this) {
    WakeWordFailure.MODEL_MISSING -> R.string.settings_wake_word_error_missing
    WakeWordFailure.MODEL_CORRUPT -> R.string.settings_wake_word_error_corrupt
    WakeWordFailure.RUNTIME -> R.string.settings_wake_word_error_runtime
}

@Composable
private fun connectionText(connection: ConnectionState): String = when (connection) {
    ConnectionState.Disconnected -> stringResource(R.string.settings_status_disconnected)
    ConnectionState.Connecting -> stringResource(R.string.settings_status_connecting)
    ConnectionState.AuthFailed -> stringResource(R.string.settings_status_auth_failed)
    is ConnectionState.Connected -> connection.hubInfo.name.takeIf { it.isNotBlank() }
        ?.let { stringResource(R.string.settings_status_connected_named, it) }
        ?: stringResource(R.string.settings_status_connected)
}

private fun UrlError.messageRes(): Int = when (this) {
    UrlError.BLANK -> R.string.settings_error_blank
    UrlError.MALFORMED -> R.string.settings_error_malformed
    UrlError.UNSUPPORTED_SCHEME -> R.string.settings_error_scheme
    UrlError.CLEARTEXT_NOT_LOCAL -> R.string.settings_error_cleartext
}
