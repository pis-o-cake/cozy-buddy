package app.cozybuddy.hub.di

import app.cozybuddy.hub.data.scheduling.AndroidAlarmScheduler
import app.cozybuddy.hub.domain.scheduling.AlarmScheduler
import app.cozybuddy.hub.feature.alarm.AlarmRingControl
import app.cozybuddy.hub.feature.alarm.service.AndroidAlarmRingControl
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class AlarmModule {

    @Binds
    abstract fun bindAlarmScheduler(impl: AndroidAlarmScheduler): AlarmScheduler

    @Binds
    abstract fun bindAlarmRingControl(impl: AndroidAlarmRingControl): AlarmRingControl
}
