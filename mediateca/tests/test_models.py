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
