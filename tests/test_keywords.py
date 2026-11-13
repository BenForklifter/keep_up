from keywords import keywords


def texts(sentence: str, final: bool = True) -> list[str]:
    return [keyword.text for keyword in keywords(sentence, final=final)]


def test_names_places_and_nouns_are_key_words():
    found = texts("We flew to New York with my sister's dogs.")

    assert "New York" in found
    assert "sister" in found
    assert "dogs" in found


def test_mentions_of_the_same_thing_share_a_key():
    (dog,) = [k for k in keywords("The dog barked.") if k.text == "dog"]
    (dogs,) = [k for k in keywords("The dogs barked.") if k.text == "dogs"]

    assert dog.key == dogs.key == "dog"


def test_vague_words_are_not_key_words():
    found = texts("We had lots of stuff and things to do.")

    assert not {"lots", "stuff", "things"} & set(found)


def test_the_last_word_of_an_unfinished_line_is_left_out():
    assert "telescope" in texts("She bought a telescope")
    assert "telescope" not in texts("She bought a telescope", final=False)
