package com.smsgateway.app.data

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

enum class MessageStatus {
    QUEUED, SENDING, SENT, FAILED, DELIVERED;

    /** Wire name used in the backend protocol. */
    val wire: String get() = name.lowercase()
}

/** A persistent outbound SMS job. [id] is assigned by the backend (or `local-<uuid>` for test sends). */
@Entity(
    tableName = "outbound_messages",
    indices = [Index("status", "nextAttemptAt"), Index("updatedAt")],
)
data class OutboundMessage(
    @PrimaryKey val id: String,
    val to: String,
    val body: String,
    val simSlot: Int? = null,
    val subscriptionId: Int? = null,
    val status: MessageStatus = MessageStatus.QUEUED,
    val attempts: Int = 0,
    val errorCode: Int? = null,
    val errorMessage: String? = null,
    /** Multipart bookkeeping for the current attempt. */
    val partsTotal: Int = 0,
    val partsReported: Int = 0,
    val partsOk: Int = 0,
    val partsDelivered: Int = 0,
    /** Earliest time (epoch ms) at which the sender may pick this up. */
    val nextAttemptAt: Long = 0,
    /** True for backend jobs: status changes must be reported upstream. */
    val reportable: Boolean = true,
    /** Last status successfully handed to the backend socket. */
    val reportedStatus: MessageStatus? = MessageStatus.QUEUED,
    val createdAt: Long,
    val updatedAt: Long,
)
