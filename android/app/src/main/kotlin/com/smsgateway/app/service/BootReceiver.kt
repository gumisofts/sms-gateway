package com.smsgateway.app.service

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.smsgateway.app.GatewayApp
import kotlinx.coroutines.launch

/** Restarts the gateway after reboot / app update if the user had it switched on. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val container = (context.applicationContext as GatewayApp).container
        val pending = goAsync()
        container.appScope.launch {
            try {
                if (container.settings.current().serviceEnabled) GatewayService.start(context.applicationContext)
            } finally {
                pending.finish()
            }
        }
    }
}
