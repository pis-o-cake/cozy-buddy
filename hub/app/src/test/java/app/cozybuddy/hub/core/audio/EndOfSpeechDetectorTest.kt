package app.cozybuddy.hub.core.audio

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** 가짜 VAD 가 주는 확률 열을 검출기에 먹여 종료 판단만 검증한다. ONNX 는 돌리지 않는다. */
class EndOfSpeechDetectorTest {

    private val frameMs = CaptureFormat.FRAME_MS

    private fun run(detector: EndOfSpeechDetector, probabilities: List<Float>): Pair<Int, VadDecision> {
        probabilities.forEachIndexed { i, p ->
            val decision = detector.onFrame(p)
            if (decision != VadDecision.CONTINUE) return (i + 1) to decision
        }
        return probabilities.size to VadDecision.CONTINUE
    }

    private fun speech(frames: Int) = List(frames) { 0.9f }
    private fun silence(frames: Int) = List(frames) { 0.05f }

    @Test
    fun `speech then 700 ms of silence ends the utterance`() {
        val needed = (700 + frameMs - 1) / frameMs
        val (frames, decision) = run(EndOfSpeechDetector(), speech(10) + silence(needed + 10))

        assertEquals(VadDecision.END_OF_SPEECH, decision)
        assertEquals(10 + needed, frames)
    }

    @Test
    fun `silence shorter than 700 ms does not end the utterance`() {
        val shortGap = 700 / frameMs - 1
        val (_, decision) = run(EndOfSpeechDetector(), speech(5) + silence(shortGap) + speech(5))

        assertEquals(VadDecision.CONTINUE, decision)
    }

    @Test
    fun `speech resuming resets the silence counter`() {
        val almost = 700 / frameMs - 1
        val sequence = speech(5) + silence(almost) + speech(1) + silence(almost)

        assertEquals(VadDecision.CONTINUE, run(EndOfSpeechDetector(), sequence).second)
    }

    @Test
    fun `no speech for 8 seconds times out`() {
        val (frames, decision) = run(EndOfSpeechDetector(), silence(400))

        assertEquals(VadDecision.NO_SPEECH, decision)
        assertEquals(8_000 / frameMs, frames)
    }

    @Test
    fun `a single noisy frame is not treated as speech start`() {
        val detector = EndOfSpeechDetector()
        val sequence = silence(10) + listOf(0.9f) + silence(400)

        val (_, decision) = run(detector, sequence)

        assertEquals(VadDecision.NO_SPEECH, decision)
        assertFalse(detector.hasSpeech)
    }

    @Test
    fun `continuous speech is cut at the 15 second maximum`() {
        val (frames, decision) = run(EndOfSpeechDetector(), speech(1_000))

        assertEquals(VadDecision.MAX_LENGTH, decision)
        assertEquals((15_000 + frameMs - 1) / frameMs, frames)
    }

    @Test
    fun `probabilities between the two thresholds count as speech and hold the utterance open`() {
        val hysteresis = List(100) { 0.4f }
        val (_, decision) = run(EndOfSpeechDetector(), speech(5) + hysteresis)

        assertEquals(VadDecision.CONTINUE, decision)
    }

    @Test
    fun `speech starts only after enough consecutive speech frames`() {
        val detector = EndOfSpeechDetector()
        detector.onFrame(0.9f)
        assertFalse(detector.hasSpeech)
        detector.onFrame(0.9f)
        assertTrue(detector.hasSpeech)
    }
}
