package com.smsgateway.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.lifecycle.viewmodel.initializer
import com.smsgateway.app.ui.MainScreen
import com.smsgateway.app.ui.connect.ConnectViewModel
import com.smsgateway.app.ui.home.HomeViewModel

class MainActivity : ComponentActivity() {
    private val container get() = (application as GatewayApp).container

    private val connectViewModel: ConnectViewModel by viewModels {
        viewModelFactory { initializer { ConnectViewModel(container) } }
    }
    private val homeViewModel: HomeViewModel by viewModels {
        viewModelFactory { initializer { HomeViewModel(container) } }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent { MainScreen(connectViewModel, homeViewModel) }
    }
}
