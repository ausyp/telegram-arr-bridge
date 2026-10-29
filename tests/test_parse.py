from bridge.parse import (
    is_join_button,
    is_retry_button,
    parse_file_button,
    release_title,
    start_param,
    strip_year,
)


def test_file_button_from_video():
    size, name = parse_file_button("2.57 GB • Dhoomakethu (2026) Malayalam...")
    assert size == int(2.57 * 1024**3)
    assert name == "Dhoomakethu (2026) Malayalam"


def test_file_button_other_styles():
    assert parse_file_button("[1.4GB] Aaru Aanugal (2026) 720p x264.mkv")[1] == "Aaru Aanugal (2026) 720p x264.mkv"
    assert parse_file_button("📂 861.56 MB - Movie Name 2024 HDRip")[1] == "Movie Name 2024 HDRip"


def test_navigation_buttons_are_not_files():
    for text in ["Files: 300", "Next ⏩", "1/30", "Pages", "Tips", "Info", "Malayalam"]:
        assert parse_file_button(text) is None


def test_release_title_guesses_missing_resolution():
    assert release_title("Dhoomakethu (2026) Malayalam H", int(1.47 * 1024**3)) == "Dhoomakethu (2026) Malayalam H 720p"
    assert release_title("Movie (2026) 1080p WEB-DL.mkv", 3 * 1024**3) == "Movie (2026) 1080p WEB-DL"


def test_gate_buttons():
    assert is_join_button("Join Channel 1", "https://t.me/+Tjnvgs97dO9hOWI1")
    assert not is_join_button("Join Channel 1", None)
    assert is_retry_button("🔄 Try Again")
    assert not is_retry_button("Check spelling on Google")


def test_start_param_and_year():
    assert start_param("https://t.me/SophiaBot?start=file_abc") == ("SophiaBot", "file_abc")
    assert start_param("https://short.link/xyz") is None
    assert strip_year("Aaru Aanugal 2026") == "Aaru Aanugal"
    assert strip_year("Aaru Aanugal") is None
