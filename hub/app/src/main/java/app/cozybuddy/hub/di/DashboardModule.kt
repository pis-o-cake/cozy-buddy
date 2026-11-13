package app.cozybuddy.hub.di

import app.cozybuddy.hub.data.home.DeviceCommandSender
import app.cozybuddy.hub.data.home.LinkDeviceCommandSender
import dagger.Binds
import dagger.Module
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent

@Module
@InstallIn(SingletonComponent::class)
abstract class DashboardModule {

    @Binds
    abstract fun bindDeviceCommandSender(impl: LinkDeviceCommandSender): DeviceCommandSender
}
