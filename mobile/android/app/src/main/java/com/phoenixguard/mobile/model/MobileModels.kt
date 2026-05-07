package com.phoenixguard.mobile.model

import android.net.Uri
import com.squareup.moshi.Json

data class MobileConfigResponse(
    val product: MobileProduct = MobileProduct(),
    val pipeline: MobilePipeline = MobilePipeline(),
    val limits: MobileLimits = MobileLimits(),
)

data class MobileProduct(
    val name: String = "PhoenixGuard Mobile",
    val subtitle: String = "Premium Quartet Desk",
)

data class MobilePipeline(
    @Json(name = "required_uploads") val requiredUploads: Int = 4,
    @Json(name = "upload_order") val uploadOrder: List<MobileUploadSlot> = emptyList(),
    @Json(name = "timeframe_choices") val timeframeChoices: List<String> = listOf("M5", "M15"),
    @Json(name = "overlay_choices") val overlayChoices: List<String> = listOf("history-plus-projection"),
    @Json(name = "council_scope_choices") val councilScopeChoices: List<String> = listOf("standard"),
    @Json(name = "default_settings") val defaultSettings: MobileRenderConfig = MobileRenderConfig(),
)

data class MobileLimits(
    @Json(name = "max_upload_bytes") val maxUploadBytes: Long = 12L * 1024L * 1024L,
    @Json(name = "min_dimension") val minDimension: Int = 64,
    @Json(name = "max_dimension") val maxDimension: Int = 8192,
)

data class MobileUploadSlot(
    val key: String = "",
    val label: String = "",
)

data class MobileRenderConfig(
    @Json(name = "overlay_mode") val overlayMode: String = "history-plus-projection",
    @Json(name = "min_conf_global") val minConfGlobal: Double = 0.42,
    @Json(name = "min_conf_latest") val minConfLatest: Double = 0.50,
    @Json(name = "history_depth") val historyDepth: Int = 8,
    @Json(name = "label_density") val labelDensity: Int = 10,
    @Json(name = "projection_focus") val projectionFocus: Double = 0.35,
    @Json(name = "debug_depth") val debugDepth: Int = 6,
    @Json(name = "fuse_timeframe_overlays") val fuseTimeframeOverlays: Boolean = false,
    @Json(name = "higher_timeframe") val higherTimeframe: String = "M15",
    @Json(name = "lower_timeframe") val lowerTimeframe: String = "M5",
    @Json(name = "council_scope") val councilScope: String = "standard",
)

data class MobileJobListResponse(
    val jobs: List<MobileJobResponse> = emptyList(),
)

data class MobileJobResponse(
    @Json(name = "job_id") val jobId: String = "",
    val status: String = "",
    @Json(name = "created_at") val createdAt: String = "",
    @Json(name = "updated_at") val updatedAt: String = "",
    @Json(name = "started_at") val startedAt: String = "",
    @Json(name = "completed_at") val completedAt: String = "",
    @Json(name = "last_error") val lastError: String = "",
    val settings: MobileRenderConfig = MobileRenderConfig(),
    @Json(name = "upload_order") val uploadOrder: List<MobileUploadSlot> = emptyList(),
    val uploads: List<MobileUploadDescriptor> = emptyList(),
    val artifacts: List<MobileArtifact> = emptyList(),
    val result: MobileAnalysisResult? = null,
)

data class MobileUploadDescriptor(
    @Json(name = "slot_index") val slotIndex: Int = 0,
    @Json(name = "slot_key") val slotKey: String = "",
    @Json(name = "slot_label") val slotLabel: String = "",
    @Json(name = "original_name") val originalName: String = "",
    val width: Int = 0,
    val height: Int = 0,
)

data class MobileAnalysisResult(
    @Json(name = "job_id") val jobId: String = "",
    val action: String = "",
    @Json(name = "headline_action") val headlineAction: String = "",
    @Json(name = "active_trade_state") val activeTradeState: String = "",
    @Json(name = "directional_intent") val directionalIntent: String = "",
    val confidence: Double = 0.0,
    @Json(name = "decision_state") val decisionState: String = "",
    @Json(name = "execution_permission") val executionPermission: String = "",
    @Json(name = "memory_similarity") val memorySimilarity: Double = 0.0,
    val projection: MobileProjection = MobileProjection(),
    val timestamp: String = "",
    @Json(name = "render_config") val renderConfig: MobileRenderConfig = MobileRenderConfig(),
    @Json(name = "multi_timeframe") val multiTimeframe: MobileMultiTimeframe = MobileMultiTimeframe(),
    val artifacts: List<MobileArtifact> = emptyList(),
    @Json(name = "overlay_sheet") val overlaySheet: MobileArtifact? = null,
    @Json(name = "overlay_fusion") val overlayFusion: MobileArtifact? = null,
    @Json(name = "final_source_artifact") val finalSourceArtifact: MobileArtifact? = null,
)

data class MobileProjection(
    val direction: String = "",
)

data class MobileMultiTimeframe(
    val aligned: Boolean = false,
    @Json(name = "gate_state") val gateState: String = "",
    val summary: String = "",
    val entries: List<MobileFrameEntry> = emptyList(),
)

data class MobileFrameEntry(
    val label: String = "",
    val action: String = "",
    val confidence: Double = 0.0,
    @Json(name = "projection_direction") val projectionDirection: String = "",
    @Json(name = "bias_direction") val biasDirection: String = "",
    @Json(name = "bias_strength") val biasStrength: Double = 0.0,
    val setup: String = "",
    val timeframe: String = "",
    @Json(name = "momentum_bias") val momentumBias: String = "",
    val artifacts: Map<String, MobileArtifact> = emptyMap(),
)

data class MobileArtifact(
    val name: String = "",
    val kind: String = "",
    val label: String = "",
    @Json(name = "slot_index") val slotIndex: Int = 0,
    @Json(name = "slot_key") val slotKey: String = "",
    @Json(name = "slot_label") val slotLabel: String = "",
    val url: String = "",
)

data class SelectedScreenshot(
    val slotIndex: Int,
    val slotKey: String,
    val slotLabel: String,
    val uri: Uri,
    val displayName: String,
)
