package app.cozybuddy.hub.feature.settings.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.cozybuddy.hub.R
import app.cozybuddy.hub.core.network.UrlError
import app.cozybuddy.hub.feature.settings.PairingError
import app.cozybuddy.hub.feature.settings.PairingStatus
import app.cozybuddy.hub.feature.settings.PairingUiState
import app.cozybuddy.hub.feature.settings.PairingViewModel

@Composable
fun PairingSection(viewModel: PairingViewModel, modifier: Modifier = Modifier) {
    val state by viewModel.uiState.collectAsStateWithLifecycle()
    PairingSection(
        state = state,
        onUrlChange = viewModel::onUrlChange,
        onCodeChange = viewModel::onCodeChange,
        onPair = viewModel::onPair,
        modifier = modifier,
    )
}

@Composable
fun PairingSection(
    state: PairingUiState,
    onUrlChange: (String) -> Unit,
    onCodeChange: (String) -> Unit,
    onPair: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val failure = (state.status as? PairingStatus.Failed)?.error
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(
            stringResource(R.string.pairing_title),
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.onBackground,
            modifier = Modifier.padding(top = 16.dp),
        )
        Text(stringResource(R.string.pairing_hint), color = MaterialTheme.colorScheme.onSurfaceVariant)
        OutlinedTextField(
            value = state.url,
            onValueChange = onUrlChange,
            label = { Text(stringResource(R.string.settings_url_label)) },
            placeholder = { Text(stringResource(R.string.pairing_url_hint)) },
            isError = failure is PairingError.Url,
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth(),
        )
        OutlinedTextField(
            value = state.code,
            onValueChange = onCodeChange,
            label = { Text(stringResource(R.string.pairing_code_label)) },
            isError = failure == PairingError.CodeFormat || failure == PairingError.InvalidCode,
            singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.NumberPassword),
            modifier = Modifier.fillMaxWidth(),
        )
        Button(onClick = onPair, enabled = !state.inProgress) {
            Text(stringResource(if (state.inProgress) R.string.pairing_in_progress else R.string.pairing_button))
        }
        when (val status = state.status) {
            is PairingStatus.Paired -> Text(
                stringResource(R.string.pairing_success, status.clientId),
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            is PairingStatus.Failed -> Text(
                stringResource(status.error.messageRes()),
                color = MaterialTheme.colorScheme.error,
            )
            PairingStatus.Idle, PairingStatus.InProgress -> Unit
        }
    }
}

private fun PairingError.messageRes(): Int = when (this) {
    is PairingError.Url -> error.messageRes()
    PairingError.CodeFormat -> R.string.pairing_error_code_format
    PairingError.InvalidCode -> R.string.pairing_error_invalid_code
    PairingError.RateLimited -> R.string.pairing_error_rate_limited
    PairingError.Network -> R.string.pairing_error_network
    PairingError.Server -> R.string.pairing_error_server
    PairingError.Storage -> R.string.pairing_error_storage
}

private fun UrlError.messageRes(): Int = when (this) {
    UrlError.BLANK -> R.string.settings_error_blank
    UrlError.MALFORMED -> R.string.settings_error_malformed
    UrlError.UNSUPPORTED_SCHEME -> R.string.pairing_error_scheme
    UrlError.CLEARTEXT_NOT_LOCAL -> R.string.settings_error_cleartext
}
