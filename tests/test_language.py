from pipecat.transcriptions.language import Language

from app.services import language as lang


def test_override_english_variants():
    for t in ["please continue in English", "switch to English", "English mein bolo", "speak english"]:
        assert lang.parse_language_override(t) == "en", t


def test_override_hindi_variants():
    for t in ["Hindi mein bolo", "hindi me bolo", "Hindi mein baat karo", "hindi me baat karo", "switch to hindi"]:
        assert lang.parse_language_override(t) == "hi", t


def test_no_override_on_plain_hinglish():
    assert lang.parse_language_override("haan mai abhi ghar ja raha hu") is None


def test_resolution_precedence_override_wins():
    st = lang.new_language_state()
    st["last_user_language"] = "hi"
    assert lang.resolve_response_language(st) == "hi"
    st["explicit_language_override"] = "en"
    assert lang.resolve_response_language(st) == "en"


def test_update_state_detects_and_counts():
    st = lang.new_language_state()
    lang.update_language_state(st, user_text="haan theek hai", detected_language="hi")
    assert st["last_user_language"] == "hi"
    assert st["response_language"] == "hi"
    assert st["turn_count"] == 1


def test_update_state_override_sticky_even_when_detected_differs():
    st = lang.new_language_state()
    lang.update_language_state(st, user_text="continue in English", detected_language="hi")
    assert st["explicit_language_override"] == "en"
    assert st["response_language"] == "en"


def test_hints_mapping_drops_unknown():
    enums = lang.language_hints_to_enums(["en", "hi", "zz"])
    assert Language.EN in enums and Language.HI in enums and len(enums) == 2


def test_language_instruction_mentions_target():
    assert "Hindi" in lang.language_instruction("hi")
    assert "English" in lang.language_instruction("en")


def test_detect_from_transcription_normalizes():
    class _F:
        language = "en-US"
    assert lang.detect_language_from_transcription(_F()) == "en"
