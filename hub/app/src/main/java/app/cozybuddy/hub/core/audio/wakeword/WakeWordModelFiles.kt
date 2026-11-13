package app.cozybuddy.hub.core.audio.wakeword

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.FileNotFoundException
import java.io.IOException
import java.security.MessageDigest
import javax.inject.Inject
import timber.log.Timber

/**
 * 번들한 모델 파일 이름과 sha256.
 *
 * 모델 출처는 openWakeWord 릴리스 v0.5.1 이다. 라이선스(모델 CC BY-NC-SA 4.0)는 `assets/licenses/openWakeWord-LICENSES.txt` 에 있다.
 * 키워드 분류기를 교체하면(예: LiveKit 으로 학습한 한국어 호출어) [KEYWORD] 와 [KEYWORD_SHA256] 만 바꾼다.
 */
object WakeWordModelFiles {
    const val ASSET_DIR = "wakeword"

    const val MELSPECTROGRAM = "melspectrogram.onnx"
    const val MELSPECTROGRAM_SHA256 = "ba2b0e0f8b7b875369a2c89cb13360ff53bac436f2895cced9f479fa65eb176f"

    const val EMBEDDING = "embedding_model.onnx"
    const val EMBEDDING_SHA256 = "70d164290c1d095d1d4ee149bc5e00543250a7316b59f31d056cff7bd3075c1f"

    const val KEYWORD = "hey_jarvis_v0.1.onnx"
    const val KEYWORD_SHA256 = "94a13cfe60075b132f6a472e7e462e8123ee70861bc3fb58434a73712ee0d2cb"

    val expectedHashes: Map<String, String> = mapOf(
        MELSPECTROGRAM to MELSPECTROGRAM_SHA256,
        EMBEDDING to EMBEDDING_SHA256,
        KEYWORD to KEYWORD_SHA256,
    )

    fun sha256Hex(bytes: ByteArray): String =
        MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }

    fun matches(name: String, bytes: ByteArray): Boolean = expectedHashes[name] == sha256Hex(bytes)
}

/** 모델 파일 읽기. 없으면 null. */
fun interface WakeWordModelStore {
    fun read(name: String): ByteArray?
}

class AssetWakeWordModelStore @Inject constructor(
    @ApplicationContext private val context: Context,
) : WakeWordModelStore {
    override fun read(name: String): ByteArray? = try {
        context.assets.open("${WakeWordModelFiles.ASSET_DIR}/$name").use { it.readBytes() }
    } catch (e: FileNotFoundException) {
        Timber.e(e, "Wake word model missing: %s", name)
        null
    } catch (e: IOException) {
        Timber.e(e, "Wake word model unreadable: %s", name)
        null
    }
}
