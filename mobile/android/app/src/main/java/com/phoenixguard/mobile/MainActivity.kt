package com.phoenixguard.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.phoenixguard.mobile.ui.PhoenixGuardMobileApp
import com.phoenixguard.mobile.ui.theme.PhoenixGuardMobileTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            PhoenixGuardMobileTheme {
                PhoenixGuardMobileApp()
            }
        }
    }
}
