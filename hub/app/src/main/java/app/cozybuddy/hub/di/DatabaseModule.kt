package app.cozybuddy.hub.di

import android.content.Context
import androidx.room.Room
import app.cozybuddy.hub.data.local.AlarmDao
import app.cozybuddy.hub.data.local.HubDatabase
import app.cozybuddy.hub.data.local.HubMigrations
import app.cozybuddy.hub.data.local.SnoozeDao
import app.cozybuddy.hub.data.repository.SnoozeRepositoryImpl
import app.cozybuddy.hub.domain.repository.SnoozeRepository
import app.cozybuddy.hub.data.repository.AlarmRepositoryImpl
import app.cozybuddy.hub.domain.repository.AlarmRepository
import dagger.Binds
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
abstract class DatabaseModule {

    @Binds
    abstract fun bindAlarmRepository(impl: AlarmRepositoryImpl): AlarmRepository

    @Binds
    abstract fun bindSnoozeRepository(impl: SnoozeRepositoryImpl): SnoozeRepository

    companion object {
        @Provides
        @Singleton
        fun provideDatabase(@ApplicationContext context: Context): HubDatabase =
            Room.databaseBuilder(context, HubDatabase::class.java, HubDatabase.NAME).addMigrations(*HubMigrations.ALL).build()

        @Provides
        fun provideAlarmDao(db: HubDatabase): AlarmDao = db.alarmDao()

        @Provides
        fun provideSnoozeDao(db: HubDatabase): SnoozeDao = db.snoozeDao()
    }
}
