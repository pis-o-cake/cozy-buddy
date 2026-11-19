"""Shared constants for the Korean wake word dataset: phrases, voices, instructions, and the train/heldout split."""

from __future__ import annotations

SAMPLE_RATE = 16_000

POSITIVE_TEXTS = ["자비스", "자비스야", "자비스,", "자비스!", "자비스?"]
COMMAND_TEXTS = ["자비스 불 꺼줘", "자비스, 음악 좀 틀어줘", "자비스 오늘 날씨 어때"]

MINI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer", "verse", "marin", "cedar"]
HELDOUT_MINI_VOICES = {"coral", "onyx", "sage", "cedar"}
TTS1_VOICES = ["alloy", "echo", "fable", "nova", "onyx", "shimmer"]
HELDOUT_TTS1_VOICES = {"onyx", "shimmer"}
TTS1_SPEEDS_TRAIN = [0.85, 1.0, 1.2]
TTS1_SPEEDS_HELDOUT = [0.7, 1.4]

INSTRUCTIONS = {
    "natural": "Speak naturally and calmly, like calling a household assistant.",
    "hurry": "Speak quickly, as if calling out in a hurry.",
    "slow": "Speak slowly and clearly, one syllable at a time.",
    "loud": "Call out loudly from across the room.",
    "soft": "Speak softly, almost whispering.",
    "child": "Speak in a cheerful young child's voice.",
    "elder": "Speak like a tired elderly person.",
    "sleepy": "Speak in a sleepy, mumbling tone.",
    "firm": "Speak in a firm, commanding tone.",
    "casual": "Speak casually, like a young adult chatting with a friend.",
}
TRAIN_INSTR = ["natural", "hurry", "slow", "loud", "soft", "child", "elder", "casual"]
HELDOUT_INSTR = ["sleepy", "firm", "natural", "loud"]

CONFUSABLES_TRAIN = [
    "서비스", "자비", "잡히스", "자비로운", "재빠르게", "자, 비스킷", "자바스크립트", "차비스", "사비스", "자비심",
    "자비를 베풀다", "비스킷", "서비스 센터", "재비", "자빠지다", "차비 줘", "짜비스", "자바", "하비스", "아비스",
]
CONFUSABLES_HELDOUT = [
    "자비 없는", "저비스", "조비스", "재비스", "장비스", "자부심", "비스트", "자, 비서", "서브스", "자바칩",
    "자비스럽다", "제비", "잠비스", "자비소", "서비스업",
]
CONFUSABLE_VOICES_TRAIN = ["alloy", "nova", "verse", "ash", "echo", "fable"]
CONFUSABLE_VOICES_HELDOUT = ["coral", "onyx", "sage", "cedar"]

# One-phoneme variants of the wake word that a listener could hear as the wake word itself; reported apart from real words.
NEAR_HOMOPHONES = {
    "차비스", "사비스", "짜비스", "하비스", "아비스", "잡히스", "저비스", "조비스", "재비스", "장비스", "잠비스",
    "자비스럽다", "자비소", "서브스",
}
