from display import _name_pattern, _name_said


def test_a_name_counts_as_a_whole_word_in_any_case():
    pattern = _name_pattern(["Mary"])

    assert _name_said(pattern, "Could you pass the salt, mary?", final=True) == "mary"
    assert _name_said(pattern, "Rosemary is in the soup.", final=True) is None


def test_the_longest_name_said_wins():
    pattern = _name_pattern(["Mary", "Aunt Mary"])

    assert _name_said(pattern, "Aunt  Mary, look!", final=True) == "Aunt  Mary"


def test_the_last_word_of_a_line_being_spoken_does_not_count_yet():
    pattern = _name_pattern(["Mary"])

    assert _name_said(pattern, "Thank you Mary", final=False) is None
    assert _name_said(pattern, "Thank you Mary and", final=False) == "Mary"


def test_without_names_nothing_matches():
    assert _name_said(_name_pattern([]), "Mary", final=True) is None
