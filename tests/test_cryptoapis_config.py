import pytest

from services import cryptoapis_config as module


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setattr(module, 'ENV_FILE', tmp_path / '.env')
    monkeypatch.delenv('CRYPTOAPIS_API_KEY', raising=False)
    monkeypatch.delenv('CRYPTOAPIS_BASE_URL', raising=False)


def test_dotenv_key_is_server_only_and_environment_takes_precedence(monkeypatch):
    module.ENV_FILE.write_text('export CRYPTOAPIS_API_KEY="local-key"\nUNRELATED_SECRET=ignored\n')
    assert module.validate_config() == {'CRYPTOAPIS_API_KEY': 'local-key',
        'CRYPTOAPIS_BASE_URL': 'https://rest.cryptoapis.io'}
    monkeypatch.setenv('CRYPTOAPIS_API_KEY', 'shell-key')
    assert module.validate_config()['CRYPTOAPIS_API_KEY'] == 'shell-key'


@pytest.mark.parametrize('url', ['http://rest.cryptoapis.io', 'https://evil.example',
    'https://rest.cryptoapis.io@evil.example', 'https://rest.cryptoapis.io?apiKey=secret',
    'https://rest.cryptoapis.io/v2', 'https://rest.cryptoapis.io:444'])
def test_key_cannot_be_sent_to_unapproved_host_or_path(monkeypatch, url):
    monkeypatch.setenv('CRYPTOAPIS_API_KEY', 'never-echo-key')
    monkeypatch.setenv('CRYPTOAPIS_BASE_URL', url)
    with pytest.raises(module.CryptoAPIsConfigurationError) as error:
        module.validate_config()
    assert 'never-echo-key' not in str(error.value) and url not in str(error.value)


@pytest.mark.parametrize('key', ['', 'bad\nkey', 'key with spaces', 'x' * 2049])
def test_missing_or_invalid_key_has_safe_actionable_error(monkeypatch, key):
    monkeypatch.setenv('CRYPTOAPIS_API_KEY', key)
    with pytest.raises(module.CryptoAPIsConfigurationError, match='CRYPTOAPIS_API_KEY') as error:
        module.validate_config()
    if key:
        assert key not in str(error.value)
