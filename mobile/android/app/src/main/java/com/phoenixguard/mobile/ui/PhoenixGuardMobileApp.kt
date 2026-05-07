package com.phoenixguard.mobile.ui

import android.content.ContentResolver
import android.net.Uri
import android.provider.OpenableColumns
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel

@Composable
fun PhoenixGuardMobileApp(
    viewModel: PhoenixGuardViewModel = viewModel(factory = PhoenixGuardViewModel.Factory),
) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var activeSlotIndex by remember { mutableIntStateOf(-1) }
    val screenshotPicker = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.PickVisualMedia(),
    ) { uri ->
        if (uri != null && activeSlotIndex >= 0) {
            val displayName = resolveDisplayName(context.contentResolver, uri) ?: "slot_${activeSlotIndex + 1}.png"
            viewModel.attachSlot(activeSlotIndex, uri, displayName)
        }
    }

    PhoenixGuardScreen(
        state = uiState,
        absoluteUrl = viewModel::absoluteUrl,
        onPickSlot = { index ->
            activeSlotIndex = index
            screenshotPicker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))
        },
        onClearSlot = viewModel::clearSlot,
        onHigherTimeframeChange = viewModel::updateHigherTimeframe,
        onLowerTimeframeChange = viewModel::updateLowerTimeframe,
        onOverlayModeChange = viewModel::updateOverlayMode,
        onCouncilScopeChange = viewModel::updateCouncilScope,
        onAnalyze = { viewModel.submit(context.contentResolver) },
        onDismissError = viewModel::dismissError,
    )
}

private fun resolveDisplayName(contentResolver: ContentResolver, uri: Uri): String? {
    val projection = arrayOf(OpenableColumns.DISPLAY_NAME)
    contentResolver.query(uri, projection, null, null, null)?.use { cursor ->
        if (cursor.moveToFirst()) {
            val index = cursor.getColumnIndex(OpenableColumns.DISPLAY_NAME)
            if (index >= 0) {
                return cursor.getString(index)
            }
        }
    }
    return null
}
