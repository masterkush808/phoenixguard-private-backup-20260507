package com.phoenixguard.mobile.ui

import android.content.ContentResolver
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.phoenixguard.mobile.BuildConfig
import com.phoenixguard.mobile.data.MobileRepository
import com.phoenixguard.mobile.data.MobileRepository.SubmissionSettings
import com.phoenixguard.mobile.model.MobileAnalysisResult
import com.phoenixguard.mobile.model.MobileConfigResponse
import com.phoenixguard.mobile.model.MobileJobResponse
import com.phoenixguard.mobile.model.SelectedScreenshot
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

private fun defaultCaptureSlots(): List<CaptureSlotUi> = listOf(
    CaptureSlotUi(0, "higher_zoomed_out", "Higher TF / Zoomed Out"),
    CaptureSlotUi(1, "higher_zoomed_in", "Higher TF / Zoomed In"),
    CaptureSlotUi(2, "lower_zoomed_out", "Lower TF / Zoomed Out"),
    CaptureSlotUi(3, "lower_zoomed_in", "Lower TF / Zoomed In"),
)

data class CaptureSlotUi(
    val index: Int,
    val key: String,
    val label: String,
    val uri: Uri? = null,
    val displayName: String = "",
)

data class PhoenixGuardUiState(
    val config: MobileConfigResponse = MobileConfigResponse(),
    val slots: List<CaptureSlotUi> = defaultCaptureSlots(),
    val higherTimeframe: String = "M15",
    val lowerTimeframe: String = "M5",
    val overlayMode: String = "history-plus-projection",
    val councilScope: String = "standard",
    val isSubmitting: Boolean = false,
    val statusLine: String = "Arrange the quartet in order, then launch analysis.",
    val activeJobId: String? = null,
    val result: MobileAnalysisResult? = null,
    val recentJobs: List<MobileJobResponse> = emptyList(),
    val errorMessage: String? = null,
) {
    val isReadyToSubmit: Boolean
        get() = slots.all { it.uri != null } && !isSubmitting
}

class PhoenixGuardViewModel(
    private val repository: MobileRepository = MobileRepository.create(BuildConfig.MOBILE_API_BASE_URL),
) : ViewModel() {
    private val _uiState = MutableStateFlow(PhoenixGuardUiState())
    val uiState: StateFlow<PhoenixGuardUiState> = _uiState.asStateFlow()

    init {
        refreshConfig()
        refreshRecentJobs()
    }

    fun attachSlot(index: Int, uri: Uri, displayName: String) {
        _uiState.update { state ->
            state.copy(
                slots = state.slots.map { slot ->
                    if (slot.index == index) slot.copy(uri = uri, displayName = displayName) else slot
                },
                errorMessage = null,
                statusLine = "Quartet staging updated. Review the order before analysis.",
            )
        }
    }

    fun clearSlot(index: Int) {
        _uiState.update { state ->
            state.copy(
                slots = state.slots.map { slot ->
                    if (slot.index == index) slot.copy(uri = null, displayName = "") else slot
                },
                statusLine = "Slot cleared. Re-place the screenshot in the correct order.",
            )
        }
    }

    fun updateHigherTimeframe(value: String) {
        _uiState.update { it.copy(higherTimeframe = value) }
    }

    fun updateLowerTimeframe(value: String) {
        _uiState.update { it.copy(lowerTimeframe = value) }
    }

    fun updateOverlayMode(value: String) {
        _uiState.update { it.copy(overlayMode = value) }
    }

    fun updateCouncilScope(value: String) {
        _uiState.update { it.copy(councilScope = value) }
    }

    fun dismissError() {
        _uiState.update { it.copy(errorMessage = null) }
    }

    fun submit(contentResolver: ContentResolver) {
        val state = _uiState.value
        val screenshots = state.slots.mapNotNull { slot ->
            val uri = slot.uri ?: return@mapNotNull null
            SelectedScreenshot(
                slotIndex = slot.index,
                slotKey = slot.key,
                slotLabel = slot.label,
                uri = uri,
                displayName = slot.displayName,
            )
        }
        if (screenshots.size != 4) {
            _uiState.update { it.copy(errorMessage = "Place all four screenshots before running the desk.") }
            return
        }
        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    isSubmitting = true,
                    errorMessage = null,
                    statusLine = "Uploading the quartet and opening a dedicated analysis job.",
                    result = null,
                )
            }
            try {
                val createdJob = repository.submitQuartet(
                    contentResolver = contentResolver,
                    screenshots = screenshots,
                    settings = SubmissionSettings(
                        overlayMode = _uiState.value.overlayMode,
                        higherTimeframe = _uiState.value.higherTimeframe,
                        lowerTimeframe = _uiState.value.lowerTimeframe,
                        councilScope = _uiState.value.councilScope,
                    ),
                )
                _uiState.update {
                    it.copy(
                        activeJobId = createdJob.jobId,
                        statusLine = "PhoenixGuard is reading the quartet. Result dossier will appear as soon as the job closes.",
                    )
                }
                val completedJob = repository.pollJob(createdJob.jobId)
                if (completedJob.status.equals("completed", ignoreCase = true) && completedJob.result != null) {
                    _uiState.update {
                        it.copy(
                            isSubmitting = false,
                            activeJobId = completedJob.jobId,
                            result = completedJob.result,
                            statusLine = "Analysis complete. Review the action, gate state, and overlay dossier.",
                        )
                    }
                    refreshRecentJobs()
                } else {
                    _uiState.update {
                        it.copy(
                            isSubmitting = false,
                            statusLine = "The job closed without a completed result.",
                            errorMessage = completedJob.lastError.ifBlank { "The mobile API did not return a completed dossier." },
                        )
                    }
                }
            } catch (exc: Exception) {
                _uiState.update {
                    it.copy(
                        isSubmitting = false,
                        statusLine = "The quartet could not be submitted cleanly.",
                        errorMessage = exc.message ?: "Unexpected mobile API error.",
                    )
                }
            }
        }
    }

    fun absoluteUrl(rawUrl: String): String = repository.absoluteUrl(rawUrl)

    private fun refreshConfig() {
        viewModelScope.launch {
            runCatching { repository.loadConfig() }
                .onSuccess { config ->
                    val defaultSettings = config.pipeline.defaultSettings
                    val configuredSlots = config.pipeline.uploadOrder.mapIndexed { index, slot ->
                        CaptureSlotUi(
                            index = index,
                            key = slot.key.ifBlank { defaultCaptureSlots().getOrNull(index)?.key.orEmpty() },
                            label = slot.label.ifBlank { defaultCaptureSlots().getOrNull(index)?.label.orEmpty() },
                        )
                    }
                    _uiState.update { state ->
                        state.copy(
                            config = config,
                            slots = if (configuredSlots.size == 4) configuredSlots else state.slots,
                            higherTimeframe = defaultSettings.higherTimeframe,
                            lowerTimeframe = defaultSettings.lowerTimeframe,
                            overlayMode = defaultSettings.overlayMode,
                            councilScope = defaultSettings.councilScope,
                        )
                    }
                }
        }
    }

    private fun refreshRecentJobs() {
        viewModelScope.launch {
            runCatching { repository.loadRecentJobs() }
                .onSuccess { jobs -> _uiState.update { it.copy(recentJobs = jobs) } }
        }
    }

    companion object {
        val Factory: ViewModelProvider.Factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T {
                return PhoenixGuardViewModel() as T
            }
        }
    }
}
