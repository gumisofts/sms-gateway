package com.smsgateway.app.ui

import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.smsgateway.app.ui.connect.ConnectScreen
import com.smsgateway.app.ui.connect.ConnectViewModel
import com.smsgateway.app.ui.connect.Route
import com.smsgateway.app.ui.home.HomeScreen
import com.smsgateway.app.ui.home.HomeViewModel

/** State-driven switch between the Connect and Home screens. */
@Composable
fun MainScreen(connectViewModel: ConnectViewModel, homeViewModel: HomeViewModel) {
    val route by connectViewModel.route.collectAsStateWithLifecycle()
    MaterialTheme {
        Surface(Modifier.safeDrawingPadding()) {
            when (route) {
                null -> {} // settings still loading
                Route.Connect -> ConnectScreen(connectViewModel)
                Route.Home -> HomeScreen(homeViewModel)
            }
        }
    }
}
