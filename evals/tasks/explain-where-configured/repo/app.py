from settings import REQUEST_TIMEOUT
from retry import RETRY_DELAY


def fetch(client, url):
    return client.get(url, timeout=REQUEST_TIMEOUT)
