import pytest
from pydantic import ValidationError

from mediateca.models import DownloadRequest


def test_download_request_accepts_http_and_https():
    assert DownloadRequest(url="http://example.com/v").url == "http://example.com/v"
    assert DownloadRequest(url="https://example.com/v").url == "https://example.com/v"


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "ftp://example.com/v",
        "not-a-url",
        "",
        "   ",
    ],
)
def test_download_request_rejects_non_http_schemes(bad_url):
    # Cierra tanto el vector de auto-XSS (javascript: en item.html) como el
    # de SSRF/lectura de archivos locales (file://) señalados en la auditoría.
    with pytest.raises(ValidationError):
        DownloadRequest(url=bad_url)


def test_download_request_defaults():
    req = DownloadRequest(url="https://example.com/v")
    assert req.audio_only is False
    assert req.quality == "best"
    assert req.format_id is None
    assert req.audio_format is None
    assert req.audio_bitrate is None


def test_download_request_accepts_format_overrides():
    req = DownloadRequest(
        url="https://example.com/v", audio_only=True, format_id="137+140",
        audio_format="flac", audio_bitrate="320",
    )
    assert req.format_id == "137+140"
    assert req.audio_format == "flac"
    assert req.audio_bitrate == "320"


def test_probe_request_same_url_validation():
    from mediateca.models import ProbeRequest

    assert ProbeRequest(url="https://example.com/v").url == "https://example.com/v"
    with pytest.raises(ValidationError):
        ProbeRequest(url="javascript:alert(1)")
