import pytest

import shared


def test_clean_names_trims_and_drops_empty_names_and_repeats():
    names = ["  Mary ", "", "mary", "Aunt   Mary"]

    assert shared.clean_names(names) == ["Mary", "Aunt Mary"]


def test_clean_names_removes_the_hotword_separators():
    assert shared.clean_names(["Mary,Grandma", "Ann/Anna"]) == ["Mary Grandma", "Ann Anna"]


def test_clean_names_limits_count_and_length():
    names = [f"Name{i}" for i in range(shared.MAX_NAMES + 2)]

    assert len(shared.clean_names(names)) == shared.MAX_NAMES
    assert shared.clean_names(["x" * 50]) == ["x" * shared.MAX_NAME_CHARS]


@pytest.mark.parametrize("value", [None, "Mary", {"names": ["Mary"]}, [1, None]])
def test_clean_names_ignores_anything_but_strings_in_a_list(value):
    assert shared.clean_names(value) == []


@pytest.mark.parametrize(
    ("direction", "color"),
    [(0, 0xFF0000), (120, 0x00FF00), (240, 0x0000FF), (360, 0xFF0000)],
)
def test_speaker_color_goes_once_around_the_color_wheel(direction, color):
    assert shared.speaker_color(direction) == color


def test_json_files_round_trip(tmp_path):
    path = tmp_path / "mic.json"
    shared.write_json(path, {"direction": 90, "muted": False})

    assert shared.read_json(path) == {"direction": 90, "muted": False}
    assert shared.read_json(tmp_path / "missing.json") is None
