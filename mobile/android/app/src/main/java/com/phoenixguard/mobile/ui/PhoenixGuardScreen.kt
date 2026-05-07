package com.phoenixguard.mobile.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.weight
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.AddPhotoAlternate
import androidx.compose.material.icons.rounded.AutoAwesome
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material.icons.rounded.NorthEast
import androidx.compose.material.icons.rounded.Schedule
import androidx.compose.material.icons.rounded.Splitscreen
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExposedDropdownMenu
import androidx.compose.material3.ExposedDropdownMenuBox
import androidx.compose.material3.ExposedDropdownMenuDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil.compose.AsyncImage
import com.phoenixguard.mobile.model.MobileAnalysisResult
import com.phoenixguard.mobile.model.MobileArtifact
import com.phoenixguard.mobile.model.MobileFrameEntry
import com.phoenixguard.mobile.model.MobileJobResponse
import com.phoenixguard.mobile.ui.theme.Bronze
import com.phoenixguard.mobile.ui.theme.BronzeSoft
import com.phoenixguard.mobile.ui.theme.Carbon
import com.phoenixguard.mobile.ui.theme.GlassLine
import com.phoenixguard.mobile.ui.theme.Ivory
import com.phoenixguard.mobile.ui.theme.MidnightSteel
import com.phoenixguard.mobile.ui.theme.Mist
import com.phoenixguard.mobile.ui.theme.Obsidian
import com.phoenixguard.mobile.ui.theme.SignalGreen
import com.phoenixguard.mobile.ui.theme.SignalRed

@Composable
fun PhoenixGuardScreen(
    state: PhoenixGuardUiState,
    absoluteUrl: (String) -> String,
    onPickSlot: (Int) -> Unit,
    onClearSlot: (Int) -> Unit,
    onHigherTimeframeChange: (String) -> Unit,
    onLowerTimeframeChange: (String) -> Unit,
    onOverlayModeChange: (String) -> Unit,
    onCouncilScopeChange: (String) -> Unit,
    onAnalyze: () -> Unit,
    onDismissError: () -> Unit,
) {
    val result = state.result
    val galleryArtifacts = remember(result) { buildGalleryArtifacts(result) }
    Scaffold(containerColor = Color.Transparent) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(Obsidian),
        ) {
            PremiumBackdrop()
            LazyColumn(
                modifier = Modifier.fillMaxSize(),
                contentPadding = PaddingValues(
                    start = 20.dp,
                    end = 20.dp,
                    top = padding.calculateTopPadding() + 18.dp,
                    bottom = padding.calculateBottomPadding() + 26.dp,
                ),
                verticalArrangement = Arrangement.spacedBy(18.dp),
            ) {
                item {
                    TopChrome(state = state)
                }
                item {
                    QuartetStage(
                        slots = state.slots,
                        onPickSlot = onPickSlot,
                        onClearSlot = onClearSlot,
                    )
                }
                item {
                    ControlDeck(
                        state = state,
                        onHigherTimeframeChange = onHigherTimeframeChange,
                        onLowerTimeframeChange = onLowerTimeframeChange,
                        onOverlayModeChange = onOverlayModeChange,
                        onCouncilScopeChange = onCouncilScopeChange,
                    )
                }
                item {
                    CommandBar(
                        isSubmitting = state.isSubmitting,
                        isReady = state.isReadyToSubmit,
                        statusLine = state.statusLine,
                        onAnalyze = onAnalyze,
                    )
                }
                if (result != null) {
                    item {
                        ResultDossier(result = result)
                    }
                    item {
                        TimeframeReadout(result = result)
                    }
                    if (galleryArtifacts.isNotEmpty()) {
                        item {
                            ArtifactGallery(artifacts = galleryArtifacts, absoluteUrl = absoluteUrl)
                        }
                    }
                }
                if (state.recentJobs.isNotEmpty()) {
                    item {
                        RecentRuns(jobs = state.recentJobs)
                    }
                }
            }

            AnimatedVisibility(
                visible = state.errorMessage != null,
                enter = fadeIn(),
                exit = fadeOut(),
                modifier = Modifier
                    .align(Alignment.BottomCenter)
                    .padding(20.dp),
            ) {
                ErrorBanner(
                    message = state.errorMessage.orEmpty(),
                    onDismiss = onDismissError,
                )
            }
        }
    }
}

@Composable
private fun PremiumBackdrop() {
    Box(
        modifier = Modifier
            .fillMaxSize()
            .drawBehind {
                drawRect(
                    brush = Brush.verticalGradient(
                        colors = listOf(Obsidian, MidnightSteel, Obsidian),
                    ),
                )
                drawCircle(
                    brush = Brush.radialGradient(
                        colors = listOf(Bronze.copy(alpha = 0.18f), Color.Transparent),
                    ),
                    radius = size.minDimension * 0.55f,
                    center = center.copy(x = size.width * 0.75f, y = size.height * 0.2f),
                )
                drawCircle(
                    brush = Brush.radialGradient(
                        colors = listOf(SignalGreen.copy(alpha = 0.10f), Color.Transparent),
                    ),
                    radius = size.minDimension * 0.42f,
                    center = center.copy(x = size.width * 0.22f, y = size.height * 0.68f),
                )
            },
    )
}

@Composable
private fun TopChrome(state: PhoenixGuardUiState) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Text(
            text = state.config.product.name,
            style = MaterialTheme.typography.displayLarge,
            color = Ivory,
        )
        Text(
            text = state.config.product.subtitle.uppercase(),
            style = MaterialTheme.typography.labelLarge,
            color = BronzeSoft,
        )
        Text(
            text = state.statusLine,
            style = MaterialTheme.typography.bodyMedium,
            color = Mist,
        )
    }
}

@Composable
private fun QuartetStage(
    slots: List<CaptureSlotUi>,
    onPickSlot: (Int) -> Unit,
    onClearSlot: (Int) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionTitle(
            title = "Capture Matrix",
            subtitle = "First two frames establish the higher timeframe pair. The last two frames define the trigger pair.",
            icon = Icons.Rounded.Splitscreen,
        )
        slots.chunked(2).forEach { rowSlots ->
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                rowSlots.forEach { slot ->
                    CaptureSlotCard(
                        modifier = Modifier.weight(1f),
                        slot = slot,
                        onPick = { onPickSlot(slot.index) },
                        onClear = { onClearSlot(slot.index) },
                    )
                }
            }
        }
    }
}

@Composable
private fun CaptureSlotCard(
    modifier: Modifier,
    slot: CaptureSlotUi,
    onPick: () -> Unit,
    onClear: () -> Unit,
) {
    val isFilled = slot.uri != null
    Surface(
        modifier = modifier,
        shape = RoundedCornerShape(24.dp),
        color = MidnightSteel.copy(alpha = 0.82f),
        border = BorderStroke(1.dp, if (isFilled) Bronze.copy(alpha = 0.55f) else GlassLine),
        tonalElevation = if (isFilled) 10.dp else 0.dp,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .aspectRatio(0.95f)
                .clickable(onClick = onPick)
                .padding(14.dp),
            verticalArrangement = Arrangement.SpaceBetween,
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = slot.label,
                    style = MaterialTheme.typography.titleMedium,
                    color = Ivory,
                    modifier = Modifier.weight(1f),
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (isFilled) {
                    IconButton(onClick = onClear) {
                        Icon(Icons.Rounded.Close, contentDescription = "Clear slot", tint = BronzeSoft)
                    }
                }
            }
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f)
                    .clip(RoundedCornerShape(18.dp))
                    .background(
                        brush = Brush.linearGradient(
                            colors = listOf(Carbon.copy(alpha = 0.95f), MidnightSteel.copy(alpha = 0.75f)),
                        ),
                    ),
                    .border(BorderStroke(1.dp, GlassLine), RoundedCornerShape(18.dp)),
            ) {
                if (isFilled) {
                    AsyncImage(
                        model = slot.uri,
                        contentDescription = slot.label,
                        modifier = Modifier.fillMaxSize(),
                        contentScale = ContentScale.Crop,
                    )
                    Box(
                        modifier = Modifier
                            .align(Alignment.BottomStart)
                            .fillMaxWidth()
                            .background(
                                brush = Brush.verticalGradient(
                                    colors = listOf(Color.Transparent, Obsidian.copy(alpha = 0.88f)),
                                ),
                            )
                            .padding(12.dp),
                    ) {
                        Text(
                            text = slot.displayName.ifBlank { "Screenshot loaded" },
                            style = MaterialTheme.typography.bodyMedium,
                            color = Ivory,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                    }
                } else {
                    Column(
                        modifier = Modifier
                            .align(Alignment.Center)
                            .padding(16.dp),
                        horizontalAlignment = Alignment.CenterHorizontally,
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Icon(
                            imageVector = Icons.Rounded.AddPhotoAlternate,
                            contentDescription = null,
                            tint = BronzeSoft,
                            modifier = Modifier.size(32.dp),
                        )
                        Text(
                            text = "Tap to place screenshot",
                            style = MaterialTheme.typography.bodyMedium,
                            color = Ivory,
                        )
                        Text(
                            text = "Order is locked into the quartet desk.",
                            style = MaterialTheme.typography.labelLarge,
                            color = Mist,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun ControlDeck(
    state: PhoenixGuardUiState,
    onHigherTimeframeChange: (String) -> Unit,
    onLowerTimeframeChange: (String) -> Unit,
    onOverlayModeChange: (String) -> Unit,
    onCouncilScopeChange: (String) -> Unit,
) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionTitle(
            title = "Desk Controls",
            subtitle = "Use the same pipeline settings the workstation expects, but tuned for a touch-native surface.",
            icon = Icons.Rounded.AutoAwesome,
        )
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            CompactDropdown(
                modifier = Modifier.weight(1f),
                label = "Higher TF",
                value = state.higherTimeframe,
                options = state.config.pipeline.timeframeChoices,
                onSelected = onHigherTimeframeChange,
            )
            CompactDropdown(
                modifier = Modifier.weight(1f),
                label = "Lower TF",
                value = state.lowerTimeframe,
                options = state.config.pipeline.timeframeChoices,
                onSelected = onLowerTimeframeChange,
            )
        }
        Row(horizontalArrangement = Arrangement.spacedBy(12.dp), modifier = Modifier.fillMaxWidth()) {
            CompactDropdown(
                modifier = Modifier.weight(1f),
                label = "Overlay",
                value = state.overlayMode,
                options = state.config.pipeline.overlayChoices,
                onSelected = onOverlayModeChange,
            )
            CompactDropdown(
                modifier = Modifier.weight(1f),
                label = "Council",
                value = state.councilScope,
                options = state.config.pipeline.councilScopeChoices,
                onSelected = onCouncilScopeChange,
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CompactDropdown(
    modifier: Modifier,
    label: String,
    value: String,
    options: List<String>,
    onSelected: (String) -> Unit,
) {
    var expanded by remember { mutableStateOf(false) }
    ExposedDropdownMenuBox(
        expanded = expanded,
        onExpandedChange = { expanded = !expanded },
        modifier = modifier,
    ) {
        OutlinedTextField(
            value = value,
            onValueChange = {},
            readOnly = true,
            label = { Text(label) },
            trailingIcon = { ExposedDropdownMenuDefaults.TrailingIcon(expanded = expanded) },
            modifier = Modifier
                .menuAnchor()
                .fillMaxWidth(),
            colors = ExposedDropdownMenuDefaults.outlinedTextFieldColors(
                focusedBorderColor = Bronze,
                unfocusedBorderColor = GlassLine,
                focusedTextColor = Ivory,
                unfocusedTextColor = Ivory,
                focusedLabelColor = BronzeSoft,
                unfocusedLabelColor = Mist,
            ),
        )
        ExposedDropdownMenu(
            expanded = expanded,
            onDismissRequest = { expanded = false },
            containerColor = Carbon,
        ) {
            options.forEach { option ->
                DropdownMenuItem(
                    text = { Text(option) },
                    onClick = {
                        onSelected(option)
                        expanded = false
                    },
                )
            }
        }
    }
}

@Composable
private fun CommandBar(
    isSubmitting: Boolean,
    isReady: Boolean,
    statusLine: String,
    onAnalyze: () -> Unit,
) {
    Surface(
        shape = RoundedCornerShape(28.dp),
        color = MidnightSteel.copy(alpha = 0.84f),
        border = BorderStroke(1.dp, GlassLine),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Text(
                text = "Analysis Command",
                style = MaterialTheme.typography.titleLarge,
                color = Ivory,
            )
            Text(
                text = statusLine,
                style = MaterialTheme.typography.bodyMedium,
                color = Mist,
            )
            Button(
                onClick = onAnalyze,
                enabled = isReady || isSubmitting,
                colors = ButtonDefaults.buttonColors(
                    containerColor = Bronze,
                    contentColor = Obsidian,
                    disabledContainerColor = Carbon,
                    disabledContentColor = Mist,
                ),
                shape = RoundedCornerShape(22.dp),
                contentPadding = PaddingValues(horizontal = 18.dp, vertical = 14.dp),
            ) {
                if (isSubmitting) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(18.dp),
                        strokeWidth = 2.dp,
                        color = Obsidian,
                    )
                    Spacer(Modifier.width(10.dp))
                }
                Text(
                    text = if (isSubmitting) "Reading quartet" else "Analyze Quartet",
                    style = MaterialTheme.typography.labelLarge,
                )
            }
        }
    }
}

@Composable
private fun ResultDossier(result: MobileAnalysisResult) {
    val actionColor = actionColor(result.action)
    Surface(
        shape = RoundedCornerShape(28.dp),
        color = MidnightSteel.copy(alpha = 0.88f),
        border = BorderStroke(1.dp, GlassLine),
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .padding(20.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Text(
                text = "Signal Dossier",
                style = MaterialTheme.typography.titleLarge,
                color = Ivory,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    text = result.action,
                    style = MaterialTheme.typography.displayMedium,
                    color = actionColor,
                    modifier = Modifier.weight(1f),
                )
                Text(
                    text = "${(result.confidence * 100).toInt()}%",
                    style = MaterialTheme.typography.displayMedium,
                    color = Ivory,
                )
            }
            Text(
                text = result.multiTimeframe.summary.ifBlank { "The quartet completed, but no multi-timeframe summary was returned." },
                style = MaterialTheme.typography.bodyLarge,
                color = Ivory,
            )
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                AssistChip(
                    onClick = {},
                    label = { Text("Gate ${result.multiTimeframe.gateState.ifBlank { "watch" }}") },
                )
                AssistChip(
                    onClick = {},
                    label = { Text("Execution ${result.executionPermission.ifBlank { "pending" }}") },
                )
                AssistChip(
                    onClick = {},
                    label = { Text("Projection ${result.projection.direction.ifBlank { result.directionalIntent }}") },
                )
            }
        }
    }
}

@Composable
private fun TimeframeReadout(result: MobileAnalysisResult) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionTitle(
            title = "Frame Readout",
            subtitle = "Each frame stays visible as a distinct input so the user can audit the quartet instead of trusting a black box.",
            icon = Icons.Rounded.Schedule,
        )
        result.multiTimeframe.entries.forEach { entry ->
            FrameRow(entry = entry)
        }
    }
}

@Composable
private fun FrameRow(entry: MobileFrameEntry) {
    Surface(
        shape = RoundedCornerShape(22.dp),
        color = Carbon.copy(alpha = 0.82f),
        border = BorderStroke(1.dp, GlassLine),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            horizontalArrangement = Arrangement.SpaceBetween,
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(modifier = Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    text = entry.label,
                    style = MaterialTheme.typography.titleMedium,
                    color = Ivory,
                )
                Text(
                    text = "${entry.timeframe} | ${entry.setup} | momentum ${entry.momentumBias}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = Mist,
                )
            }
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Text(
                    text = entry.action,
                    style = MaterialTheme.typography.titleLarge,
                    color = actionColor(entry.action),
                )
                Text(
                    text = "${(entry.confidence * 100).toInt()}%",
                    style = MaterialTheme.typography.bodyMedium,
                    color = BronzeSoft,
                )
            }
        }
    }
}

@Composable
private fun ArtifactGallery(
    artifacts: List<MobileArtifact>,
    absoluteUrl: (String) -> String,
) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionTitle(
            title = "Overlay Gallery",
            subtitle = "The premium surface leans on restrained image-led review instead of dense dashboard chrome.",
            icon = Icons.Rounded.NorthEast,
        )
        LazyRow(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            items(artifacts, key = { it.name }) { artifact ->
                Surface(
                    modifier = Modifier.width(250.dp),
                    shape = RoundedCornerShape(24.dp),
                    color = MidnightSteel.copy(alpha = 0.90f),
                    border = BorderStroke(1.dp, GlassLine),
                ) {
                    Column(
                        modifier = Modifier.padding(12.dp),
                        verticalArrangement = Arrangement.spacedBy(10.dp),
                    ) {
                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .aspectRatio(1.3f)
                                .clip(RoundedCornerShape(18.dp))
                                .background(Carbon),
                        ) {
                            AsyncImage(
                                model = absoluteUrl(artifact.url),
                                contentDescription = artifact.label,
                                modifier = Modifier.fillMaxSize(),
                                contentScale = ContentScale.Crop,
                            )
                        }
                        Text(
                            text = artifact.label,
                            style = MaterialTheme.typography.titleMedium,
                            color = Ivory,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                        Text(
                            text = artifact.kind.uppercase(),
                            style = MaterialTheme.typography.labelLarge,
                            color = BronzeSoft,
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun RecentRuns(jobs: List<MobileJobResponse>) {
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        SectionTitle(
            title = "Recent Runs",
            subtitle = "Completed jobs stay visible so the operator can compare fresh dossiers without leaving the mobile desk.",
            icon = Icons.Rounded.Schedule,
        )
        jobs.take(5).forEach { job ->
            Surface(
                shape = RoundedCornerShape(20.dp),
                color = Carbon.copy(alpha = 0.78f),
                border = BorderStroke(1.dp, GlassLine),
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(16.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text(
                            text = job.result?.action ?: job.status.uppercase(),
                            style = MaterialTheme.typography.titleMedium,
                            color = actionColor(job.result?.action.orEmpty()),
                        )
                        Text(
                            text = job.completedAt.ifBlank { job.updatedAt },
                            style = MaterialTheme.typography.bodyMedium,
                            color = Mist,
                        )
                    }
                    Text(
                        text = if (job.result != null) "${(job.result.confidence * 100).toInt()}%" else job.status,
                        style = MaterialTheme.typography.titleMedium,
                        color = BronzeSoft,
                    )
                }
            }
        }
    }
}

@Composable
private fun ErrorBanner(message: String, onDismiss: () -> Unit) {
    Surface(
        shape = RoundedCornerShape(24.dp),
        color = SignalRed.copy(alpha = 0.92f),
        border = BorderStroke(1.dp, SignalRed),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                text = message,
                style = MaterialTheme.typography.bodyMedium,
                color = Obsidian,
                modifier = Modifier.weight(1f),
            )
            Spacer(modifier = Modifier.width(12.dp))
            OutlinedButton(onClick = onDismiss, border = BorderStroke(1.dp, Obsidian)) {
                Text("Dismiss", color = Obsidian)
            }
        }
    }
}

@Composable
private fun SectionTitle(title: String, subtitle: String, icon: ImageVector) {
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(icon, contentDescription = null, tint = BronzeSoft)
            Spacer(modifier = Modifier.width(8.dp))
            Text(
                text = title,
                style = MaterialTheme.typography.titleLarge,
                color = Ivory,
            )
        }
        Text(
            text = subtitle,
            style = MaterialTheme.typography.bodyMedium,
            color = Mist,
        )
    }
}

private fun actionColor(action: String): Color = when (action.uppercase()) {
    "BUY" -> SignalGreen
    "SELL" -> SignalRed
    else -> BronzeSoft
}

private fun buildGalleryArtifacts(result: MobileAnalysisResult?): List<MobileArtifact> {
    if (result == null) return emptyList()
    val artifacts = mutableListOf<MobileArtifact>()
    result.overlaySheet?.let(artifacts::add)
    result.overlayFusion?.let(artifacts::add)
    result.finalSourceArtifact?.let(artifacts::add)
    result.multiTimeframe.entries.forEach { entry ->
        entry.artifacts["overlay"]?.let(artifacts::add)
    }
    return artifacts.distinctBy { it.name }
}
