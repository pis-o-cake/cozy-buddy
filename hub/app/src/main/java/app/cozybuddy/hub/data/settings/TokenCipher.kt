package app.cozybuddy.hub.data.settings

import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import javax.inject.Inject
import javax.inject.Singleton

/** 토큰 암복호화. 구현은 키를 기기 밖으로 내보내지 않아야 한다. */
interface TokenCipher {
    fun encrypt(plain: String): String

    /** @throws java.security.GeneralSecurityException 키가 사라졌거나 값이 손상됐을 때. */
    fun decrypt(encoded: String): String
}

/**
 * Android Keystore 의 AES-256-GCM 키로 암호화한다. 저장 형식은 Base64(IV 12바이트 + 암호문).
 *
 * security-crypto 는 deprecated 라서 쓰지 않고 플랫폼 API 만 사용한다.
 */
@Singleton
class AndroidKeystoreTokenCipher @Inject constructor() : TokenCipher {

    override fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val body = cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.getEncoder().encodeToString(cipher.iv + body)
    }

    override fun decrypt(encoded: String): String {
        val raw = Base64.getDecoder().decode(encoded)
        require(raw.size > IV_BYTES) { "Encrypted token is too short" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(TAG_BITS, raw, 0, IV_BYTES))
        return String(cipher.doFinal(raw, IV_BYTES, raw.size - IV_BYTES), Charsets.UTF_8)
    }

    private fun secretKey(): SecretKey {
        val store = KeyStore.getInstance(PROVIDER).apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, PROVIDER)
        generator.init(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_BITS)
                .build(),
        )
        return generator.generateKey()
    }

    private companion object {
        const val PROVIDER = "AndroidKeyStore"
        const val ALIAS = "cozy_hub_server_token"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val KEY_BITS = 256
        const val IV_BYTES = 12
        const val TAG_BITS = 128
    }
}
