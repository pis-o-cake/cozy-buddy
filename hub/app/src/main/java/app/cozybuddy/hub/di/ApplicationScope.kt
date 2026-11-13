package app.cozybuddy.hub.di

import javax.inject.Qualifier

/** 프로세스 수명 동안 도는 코루틴 범위. 서버 연결·동기화처럼 Activity 와 무관한 작업용이다. */
@Qualifier
@Retention(AnnotationRetention.RUNTIME)
annotation class ApplicationScope
