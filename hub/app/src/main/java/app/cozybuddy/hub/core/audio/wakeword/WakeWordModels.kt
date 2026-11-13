package app.cozybuddy.hub.core.audio.wakeword

/**
 * 파이프라인이 쓰는 세 모델의 추상화. 실제 구현은 ONNX Runtime 이고 테스트는 가짜를 쓴다.
 *
 * 모든 호출은 같은 스레드에서 직렬로 일어난다.
 */
interface WakeWordModels : AutoCloseable {
    /**
     * 원시 PCM(int16 값을 정규화 없이 float 로 옮긴 것)의 멜 스펙트로그램.
     *
     * @return 프레임 수 x [OpenWakeWordFormat.MEL_BINS] 를 행 우선으로 편 배열. 스케일 변환 전 원출력이다.
     */
    fun melspectrogram(samples: FloatArray): FloatArray

    /** 변환된 멜 프레임 [OpenWakeWordFormat.EMBEDDING_WINDOW_FRAMES] 개에서 [OpenWakeWordFormat.EMBEDDING_DIM] 차원 임베딩. */
    fun embedding(melWindow: FloatArray): FloatArray

    /** 임베딩 [OpenWakeWordFormat.CLASSIFIER_FRAMES] 개를 이은 입력의 키워드 점수(0..1). */
    fun classify(embeddings: FloatArray): Float
}
