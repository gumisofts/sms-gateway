package com.smsgateway.app.data

import androidx.room.Database
import androidx.room.RoomDatabase

@Database(entities = [OutboundMessage::class], version = 1, exportSchema = true)
abstract class AppDatabase : RoomDatabase() {
    abstract fun messageDao(): MessageDao
}
