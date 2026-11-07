package com.smsgateway.app.data

import androidx.room.Dao
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.Update
import kotlinx.coroutines.flow.Flow

@Dao
interface MessageDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(message: OutboundMessage): Long

    @Update
    suspend fun update(message: OutboundMessage)

    @Query("SELECT * FROM outbound_messages WHERE id = :id")
    suspend fun get(id: String): OutboundMessage?

    @Query(
        "SELECT * FROM outbound_messages WHERE status = 'QUEUED' AND nextAttemptAt <= :now " +
            "ORDER BY nextAttemptAt, createdAt LIMIT 1",
    )
    suspend fun nextDue(now: Long): OutboundMessage?

    /** Emits whenever the table changes; used to schedule the next send wake-up. */
    @Query("SELECT MIN(nextAttemptAt) FROM outbound_messages WHERE status = 'QUEUED'")
    fun observeEarliestQueued(): Flow<Long?>

    /** Atomic QUEUED -> SENDING transition; returns rows changed (0 if someone else got it). */
    @Query(
        "UPDATE outbound_messages SET status = 'SENDING', attempts = attempts + 1, errorCode = NULL, " +
            "errorMessage = NULL, partsTotal = 0, partsReported = 0, partsOk = 0, partsDelivered = 0, " +
            "updatedAt = :now WHERE id = :id AND status = 'QUEUED'",
    )
    suspend fun claim(id: String, now: Long): Int

    @Query("SELECT * FROM outbound_messages ORDER BY createdAt DESC, id LIMIT :limit")
    fun observeRecent(limit: Int): Flow<List<OutboundMessage>>

    @Query(
        "SELECT * FROM outbound_messages WHERE reportable = 1 AND (reportedStatus IS NULL OR reportedStatus != status) " +
            "ORDER BY updatedAt LIMIT 50",
    )
    fun observeUnreported(): Flow<List<OutboundMessage>>

    @Query("UPDATE outbound_messages SET reportedStatus = :status WHERE id = :id")
    suspend fun markReported(id: String, status: MessageStatus)

    @Query("SELECT COUNT(*) FROM outbound_messages WHERE status IN ('QUEUED', 'SENDING')")
    fun observeQueueDepth(): Flow<Int>

    @Query("SELECT COUNT(*) FROM outbound_messages WHERE status IN ('QUEUED', 'SENDING')")
    suspend fun queueDepth(): Int

    @Query("SELECT * FROM outbound_messages WHERE status = 'SENDING' AND updatedAt < :before")
    suspend fun staleSending(before: Long): List<OutboundMessage>

    /** Housekeeping: drop old finished rows that have been reported. */
    @Query(
        "DELETE FROM outbound_messages WHERE status IN ('SENT', 'DELIVERED', 'FAILED') AND updatedAt < :before " +
            "AND (reportable = 0 OR reportedStatus = status)",
    )
    suspend fun deleteFinishedBefore(before: Long): Int
}
