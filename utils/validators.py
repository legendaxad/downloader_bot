from urllib.parse import urlparse


SUPPORTED_DOMAINS={
    "youtube.com",
    "youtube.be",
    "youtu.be",
    "instagram.com",
    "tiktok.com"
}

def is_valid_url(url:str)-> bool:
    try:
        parsed=urlparse(url)
        return parsed.scheme in {"http","https"} and bool(parsed.netloc)
    except ValueError:
        return False

def is_supported_platform(url: str) -> bool:
    if not is_valid_url(url):
        return False
    hostname=urlparse(url).hostname

    if not hostname:
        return False
    hostname = hostname.lower()

    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in SUPPORTED_DOMAINS
    )