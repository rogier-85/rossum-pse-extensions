from app.passwords import MAX_CANDIDATES, extract_password_candidates


def test_extracts_supported_labels_and_preserves_case() -> None:
    text = "Password: SeCrEt!\nPIN is 1234\npwd = 'two words'\npasscode-next"
    assert extract_password_candidates(text, None) == [
        "SeCrEt!",
        "1234",
        "two words",
        "next",
    ]


def test_extracts_html_and_deduplicates_plain_text() -> None:
    assert extract_password_candidates(
        "Code: SameValue",
        "<p>Password: <strong>SameValue</strong></p><p>PIN = 9999</p>",
    ) == ["SameValue", "9999"]


def test_ignores_unlabelled_words_and_limits_candidates() -> None:
    text = "random secret " + " ".join(f"code: value{index}" for index in range(30))
    candidates = extract_password_candidates(text, None)
    assert len(candidates) == MAX_CANDIDATES
    assert "secret" not in candidates
