package com.smsgateway.app.sms

enum class DeliveryClass {
    DELIVERED, PENDING, FAILED;

    companion object {
        /**
         * Classifies a GSM TP-Status byte from a status report (3GPP TS 23.040 9.2.3.15):
         * 0x00-0x1F success, 0x20-0x3F temporary (SMSC still trying), anything higher is a failure.
         * `-1` (no PDU available) is treated as delivered since the platform only fires the intent on a report.
         */
        fun fromTpStatus(status: Int): DeliveryClass = when {
            status < 0x20 -> DELIVERED
            status < 0x40 -> PENDING
            else -> FAILED
        }
    }
}
