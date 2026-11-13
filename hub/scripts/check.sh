#!/bin/sh
# 저장소 루트에서 실행: sh hub/scripts/check.sh
set -eu

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd -P)

# Gradle 9 / AGP 9 는 JDK 17 이상이 필요하다. 미설정이거나 낮은 JDK 면 Android Studio 번들 JBR 로 대체한다.
java_major() {
  [ -x "$1/bin/java" ] || { echo 0; return; }
  "$1/bin/java" -version 2>&1 | sed -n '1s/.*version "\([0-9]*\).*/\1/p'
}

if [ -z "${JAVA_HOME:-}" ] || [ "$(java_major "$JAVA_HOME")" -lt 17 ]; then
  jbr="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
  if [ ! -d "$jbr" ]; then
    echo "error: JDK 17+ is required; set JAVA_HOME or install Android Studio" >&2
    exit 1
  fi
  JAVA_HOME=$jbr
  export JAVA_HOME
fi

# local.properties 는 gitignore 대상이므로 SDK 위치를 환경변수로도 받고, 없으면 Android Studio 기본 위치를 쓴다.
if [ ! -f "$root/hub/local.properties" ] && [ -z "${ANDROID_HOME:-}" ]; then
  if [ -d "$HOME/Library/Android/sdk" ]; then
    ANDROID_HOME="$HOME/Library/Android/sdk"
    export ANDROID_HOME
  else
    echo "error: set ANDROID_HOME or create hub/local.properties with sdk.dir" >&2
    exit 1
  fi
fi

"$root/hub/gradlew" --no-daemon -p "$root/hub" :app:testDebugUnitTest :app:assembleDebug
