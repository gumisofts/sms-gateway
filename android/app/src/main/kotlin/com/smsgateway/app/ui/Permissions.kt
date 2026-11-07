package com.smsgateway.app.ui

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.PowerManager
import androidx.core.content.ContextCompat

/** Everything the gateway wants. POST_NOTIFICATIONS is optional: the service works without it. */
fun requiredPermissions(): List<String> = buildList {
    add(Manifest.permission.SEND_SMS)
    add(Manifest.permission.READ_PHONE_STATE)
    if (Build.VERSION.SDK_INT >= 33) add(Manifest.permission.POST_NOTIFICATIONS)
}

/** Without these the gateway cannot do its job. */
val criticalPermissions = setOf(Manifest.permission.SEND_SMS, Manifest.permission.READ_PHONE_STATE)

fun Context.missingPermissions(): List<String> =
    requiredPermissions().filter { ContextCompat.checkSelfPermission(this, it) != PackageManager.PERMISSION_GRANTED }

fun Context.ignoringBatteryOptimizations(): Boolean =
    getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

fun permissionLabel(permission: String): String = when (permission.substringAfterLast('.')) {
    "SEND_SMS" -> "Send SMS"
    "READ_PHONE_STATE" -> "Phone state (SIM info)"
    "POST_NOTIFICATIONS" -> "Notifications"
    else -> permission.substringAfterLast('.')
}
