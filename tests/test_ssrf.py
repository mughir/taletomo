import pytest
from taletomo.providers.security import SSRFValidationError, validate_provider_endpoint


def test_ssrf_rejects_localhost_and_loopback():
    with pytest.raises(SSRFValidationError):
        validate_provider_endpoint("http://localhost:8000/v1")

    with pytest.raises(SSRFValidationError):
        validate_provider_endpoint("http://127.0.0.1:8000/v1")


def test_ssrf_rejects_cloud_metadata():
    with pytest.raises(SSRFValidationError):
        validate_provider_endpoint("http://169.254.169.254/latest/meta-data/")


def test_ssrf_rejects_private_networks():
    with pytest.raises(SSRFValidationError):
        validate_provider_endpoint("http://10.0.0.1:8080/v1")

    with pytest.raises(SSRFValidationError):
        validate_provider_endpoint("http://192.168.1.50/v1")


def test_ssrf_rejects_invalid_scheme():
    with pytest.raises(SSRFValidationError):
        validate_provider_endpoint("ftp://api.openai.com/v1")


def test_ssrf_allows_safe_endpoint():
    assert validate_provider_endpoint("https://api.openai.com/v1") is True
