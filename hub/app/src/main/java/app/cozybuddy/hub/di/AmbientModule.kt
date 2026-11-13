package app.cozybuddy.hub.di

import app.cozybuddy.hub.core.time.SystemTimeSource
import app.cozybuddy.hub.core.time.TimeSource
import app.cozybuddy.hub.feature.ambient.AndroidClockDisplayConfig
import app.cozybuddy.hub.feature.ambient.ClockDisplayConfig
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class AmbientModule {

    @Binds
    abstract fun bindTimeSource(impl: SystemTimeSource): TimeSource

    @Binds
    abstract fun bindClockDisplayConfig(impl: AndroidClockDisplayConfig): ClockDisplayConfig
}
