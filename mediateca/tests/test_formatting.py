from mediateca.formatting import fmt_duration, fmt_size


def test_fmt_duration_none_and_zero():
    assert fmt_duration(None) == "—"
    assert fmt_duration(0) == "—"


def test_fmt_duration_under_an_hour():
    assert fmt_duration(65) == "1:05"


def test_fmt_duration_with_hours():
    assert fmt_duration(3725) == "1:02:05"


def test_fmt_size_none_and_zero():
    assert fmt_size(None) == "—"
    assert fmt_size(0) == "—"


def test_fmt_size_units():
    assert fmt_size(500) == "500.0 B"
    assert fmt_size(1536) == "1.5 KB"
    assert fmt_size(5 * 1024 * 1024) == "5.0 MB"
