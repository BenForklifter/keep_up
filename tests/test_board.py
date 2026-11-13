from board import MIN_DWELL, KeywordBoard
from keywords import Keyword


def kw(text: str, weight: float = 2.0) -> Keyword:
    return Keyword(text.lower(), text, weight)


def shown(board: KeywordBoard, now: float) -> list[str | None]:
    return [word.text if word else None for word in board.words(now)]


def test_new_words_fill_the_free_slots_strongest_first():
    board = KeywordBoard(3)
    board.hear(0, [kw("hotel", 1.5), kw("Lisbon", 3.0)], now=0.0)

    assert shown(board, 0.0) == ["Lisbon", "hotel", None]


def test_a_new_word_replaces_the_weakest_once_that_was_readable():
    board = KeywordBoard(1)
    board.hear(0, [kw("wedding")], now=0.0)
    assert shown(board, 0.0) == ["wedding"]

    board.hear(1, [kw("dress")], now=0.1)
    assert shown(board, 0.1) == ["wedding"]
    assert shown(board, MIN_DWELL) == ["dress"]


def test_a_word_that_keeps_coming_up_outlasts_a_newer_one():
    board = KeywordBoard(1)
    for line_id, said_at in enumerate([0.0, 1.0, 2.0]):
        board.hear(line_id, [kw("wedding")], now=said_at)
    board.hear(3, [kw("dress")], now=2.5)

    assert shown(board, 2.5) == ["wedding"]


def test_a_word_revised_away_leaves_the_board():
    board = KeywordBoard(2)
    board.hear(0, [kw("dis")], now=0.0)
    assert shown(board, 0.0) == ["dis", None]

    board.hear(0, [kw("display")], now=0.2)
    assert shown(board, 0.2) == ["display", None]


def test_shrinking_keeps_the_strongest_words_in_place():
    board = KeywordBoard(3)
    board.hear(0, [kw("hotel", 1.5), kw("Lisbon", 3.0), kw("dress", 2.0)], now=0.0)
    assert shown(board, 0.0) == ["Lisbon", "dress", "hotel"]

    board.resize(2, now=0.0)
    assert shown(board, 0.0) == ["Lisbon", "dress"]


def test_age_counts_from_the_last_mention():
    board = KeywordBoard(1)
    board.hear(0, [kw("hotel")], now=0.0)
    board.hear(1, [kw("hotel")], now=2.0)

    (word,) = board.words(3.0)
    assert word.age == 1.0
