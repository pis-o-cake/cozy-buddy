package app.cozybuddy.hub.di

import android.content.Context
import androidx.room.Room
import app.cozybuddy.hub.data.local.AlarmDao
import app.cozybuddy.hub.data.local.HubDatabase
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

    companion object {
        @Provides
        @Singleton
        fun provideDatabase(@ApplicationContext context: Context): HubDatabase =
            Room.databaseBuilder(context, HubDatabase::class.java, HubDatabase.NAME).build()

        @Provides
        fun provideAlarmDao(db: HubDatabase): AlarmDao = db.alarmDao()
    }
}
