import eval as evaluation  # tools/eval/eval.py

SCENARIO = {
    "name": "lisbon",
    "expect": [{"lisbon"}, {"flight", "flights"}],
    "ok": {"hotel"},
    "lines": [("A", "We fly to Lisbon."), ("B", "Is the flight early?")],
}


def test_word_error_rate():
    assert evaluation.wer(["a", "b", "c"], ["a", "b", "c"]) == 0
    assert evaluation.wer(["a", "b", "c", "d"], ["a", "x", "c"]) == 0.5


def test_score_reads_what_the_app_logs():
    log = [
        (0.0, 'speech: heard "Said in the room before the test."  [2.0 s, last update 9 ms]'),
        (1.0, "test: audio 20261113-120000_lisbon"),
        (2.0, 'speech: heard "We fly to Lisbon."  [1.5 s, last update 20 ms]'),
        (2.5, "keywords: Lisbon"),
        (3.0, 'speech: heard "Is the flight early?"  [1.2 s, last update 30 ms]'),
        (3.5, "keywords: flight, Paris"),
        (3.6, "speech: falling behind, dropped 1.5 s of audio"),
        (4.0, "test: end 20261113-120000_lisbon"),
    ]

    result = evaluation.score(SCENARIO, log, "[3] Lisbon    hotel", duration=3.0)

    assert result["transcript"] == ["We fly to Lisbon.", "Is the flight early?"]
    assert result["wer"] == 0
    assert result["keyword_recall"] == 1
    assert result["invented_keywords"] == ["paris"]
    assert result["dropped_audio_s"] == 1.5
    assert result["avg_update_ms"] == 25
    assert result["screen_level"] == "3"
    assert result["screen_ok"] == 1
