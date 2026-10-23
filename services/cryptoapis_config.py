"""Server-only Crypto APIs settings; credentials never come from a browser request."""
import os
from pathlib import Path

BASE_URL = 'https://rest.cryptoapis.io'
ENV_FILE = Path(__file__).resolve().parents[1] / '.env'


class CryptoAPIsConfigurationError(ValueError):
    pass


def config():
    keys = {'CRYPTOAPIS_API_KEY', 'CRYPTOAPIS_BASE_URL'}
    values = {}
    if ENV_FILE.is_file():
        for raw in ENV_FILE.read_text(encoding='utf-8').splitlines():
            key, sep, value = raw.strip().removeprefix('export ').partition('=')
            if sep and key.strip() in keys:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in '\"\'':
                    value = value[1:-1]
                values[key.strip()] = value
    return {key: os.environ.get(key, values.get(key, BASE_URL if key == 'CRYPTOAPIS_BASE_URL' else '')) for key in keys}


def validate_config():
    values = config()
    key = values['CRYPTOAPIS_API_KEY']
    if not key:
        raise CryptoAPIsConfigurationError('Isi CRYPTOAPIS_API_KEY pada .env untuk melanjutkan tracing hop 4–6')
    if len(key) > 2048 or any(ord(char) < 33 or ord(char) > 126 for char in key):
        raise CryptoAPIsConfigurationError('Format CRYPTOAPIS_API_KEY tidak valid; periksa .env')
    base_url = (values['CRYPTOAPIS_BASE_URL'] or BASE_URL).rstrip('/')
    if base_url != BASE_URL:
        raise CryptoAPIsConfigurationError('CRYPTOAPIS_BASE_URL harus endpoint HTTPS resmi https://rest.cryptoapis.io tanpa path atau query')
    return {**values, 'CRYPTOAPIS_BASE_URL': base_url}
