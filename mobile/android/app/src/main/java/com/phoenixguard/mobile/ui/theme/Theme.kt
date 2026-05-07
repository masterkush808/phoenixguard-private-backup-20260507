package com.phoenixguard.mobile.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable

private val PhoenixDarkScheme = darkColorScheme(
    primary = Bronze,
    onPrimary = Obsidian,
    secondary = BronzeSoft,
    onSecondary = Obsidian,
    tertiary = SignalGreen,
    background = Obsidian,
    onBackground = Ivory,
    surface = MidnightSteel,
    onSurface = Ivory,
    surfaceVariant = Carbon,
    onSurfaceVariant = Mist,
    error = SignalRed,
)

@Composable
fun PhoenixGuardMobileTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = PhoenixDarkScheme,
        typography = PhoenixTypography,
        content = content,
    )
}
