from app.services import memory as mem


def test_extract_pinned_accepts_durable_statement():
    out = mem.extract_pinned_facts("I want low latency replies", [], max_pinned=12)
    assert out == ["I want low latency replies"]


def test_extract_pinned_rejects_filler():
    assert mem.extract_pinned_facts("haan ok", [], max_pinned=12) == []
    assert mem.extract_pinned_facts("wait", [], max_pinned=12) == []
    assert mem.extract_pinned_facts("bro listen", [], max_pinned=12) == []


def test_extract_pinned_dedupes_and_caps():
    existing = ["I want low latency replies"]
    same = mem.extract_pinned_facts("i want low latency replies", existing, max_pinned=12)
    assert same == existing  # case-insensitive dedupe
    capped = existing
    for i in range(20):
        capped = mem.extract_pinned_facts(f"please keep fact number {i} in mind", capped, max_pinned=5)
    assert len(capped) == 5


def test_recall_trigger():
    assert mem.recall_triggered("what did I say earlier about the voice?")
    assert mem.recall_triggered("as I mentioned, use Hindi")
    assert not mem.recall_triggered("okay let's continue")


def test_should_update_summary_threshold():
    cfg = {"enable_running_summary_after_turn": 10, "summary_update_every_turns": 5}
    assert not mem.should_update_summary(9, cfg)
    assert mem.should_update_summary(10, cfg)
    assert not mem.should_update_summary(11, cfg)
    assert mem.should_update_summary(15, cfg)


def test_build_running_summary_compacts_older_only():
    transcript = [{"role": "user", "text": f"point {i}"} for i in range(10)]
    transcript += [{"role": "assistant", "text": "ok"}]
    summary = mem.build_running_summary(transcript, recent_turn_pairs=3, max_summary_tokens=250)
    assert summary.startswith("Earlier in this call the user said:")
    assert "point 0" in summary
    assert "point 9" not in summary  # most recent 3 excluded


def test_build_running_summary_respects_token_budget():
    transcript = [{"role": "user", "text": "word " * 50} for _ in range(20)]
    summary = mem.build_running_summary(transcript, recent_turn_pairs=2, max_summary_tokens=20)
    assert len(summary) <= 20 * 4 + 40  # budget (chars) + prefix slack


def test_trim_recent_messages_keeps_two_per_pair():
    msgs = [{"role": "system", "content": "s"}]
    for i in range(10):
        msgs.append({"role": "user", "content": f"u{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    trimmed = mem.trim_recent_messages(msgs, recent_turn_pairs=3)
    assert len(trimmed) == 6
    assert all(m["role"] != "system" for m in trimmed)
    assert trimmed[-1] == {"role": "assistant", "content": "a9"}


def test_build_system_block_includes_sections():
    block = mem.build_system_block(
        "Base prompt.",
        language_policy="Begin every reply with <en> or <hi>.",
        language_hint="(Weak hint: user looked like Hindi.)",
        pinned_facts=["User wants low latency."],
        running_summary="Earlier the user discussed voice config.",
        recall_snippets=["use hindi"],
    )
    assert "Base prompt." in block
    assert "<en> or <hi>" in block
    assert "Weak hint" in block
    assert "User wants low latency." in block
    assert "Earlier the user discussed voice config." in block
    assert "use hindi" in block
