"""Chain-scoped scanners and validated public explorer links."""
from hashlib import sha256
import re

CHAIN_CONFIG = {
    'ETHEREUM': {'chain_id': 1, 'native_symbol': 'ETH', 'explorer_url': 'https://etherscan.io', 'fallback_url': 'https://eth.blockscout.com/api'},
    'BSC': {'chain_id': 56, 'native_symbol': 'BNB', 'explorer_url': 'https://bscscan.com', 'fallback_url': None},
    'POLYGON': {'chain_id': 137, 'native_symbol': 'POL', 'explorer_url': 'https://polygonscan.com', 'fallback_url': 'https://polygon.blockscout.com/api'},
    'ARBITRUM': {'chain_id': 42161, 'native_symbol': 'ETH', 'explorer_url': 'https://arbiscan.io', 'fallback_url': 'https://arbitrum.blockscout.com/api'},
    'OPTIMISM': {'chain_id': 10, 'native_symbol': 'ETH', 'explorer_url': 'https://optimistic.etherscan.io', 'fallback_url': 'https://optimism.blockscout.com/api'},
    'BASE': {'chain_id': 8453, 'native_symbol': 'ETH', 'explorer_url': 'https://basescan.org', 'fallback_url': 'https://base.blockscout.com/api'},
    'TRON': {'chain_id': None, 'native_symbol': 'TRX', 'explorer_url': 'https://tronscan.org', 'fallback_url': None},
    'BTC': {'chain_id': None, 'native_symbol': 'BTC', 'explorer_url': 'https://mempool.space', 'fallback_url': 'https://blockstream.info/api'},
    'SOLANA': {'chain_id': None, 'native_symbol': 'SOL', 'explorer_url': 'https://solscan.io', 'fallback_url': None},
}
for _chain, _config in CHAIN_CONFIG.items():
    _config['chain_family'] = 'BITCOIN' if _chain == 'BTC' else 'SOLANA' if _chain == 'SOLANA' else 'TRON' if _chain == 'TRON' else 'EVM'
    _config['scanner_provider'] = 'MEMPOOL_SPACE' if _chain == 'BTC' else 'SOLANA_MAINNET_RPC' if _chain == 'SOLANA' else 'SCENARIO_FIXTURE' if _chain == 'TRON' else 'ETHERSCAN_V2'
ALIASES = {'ETHEREUM_ERC20': 'ETHEREUM', 'BSC_BEP20': 'BSC', 'TRON_TRC20': 'TRON', 'BITCOIN': 'BTC', 'SOL': 'SOLANA'}
BASE58 = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
BECH32 = 'qpzry9x8gf2tvdw0s3jn54khce6mua7l'


def normalize_chain(chain):
    if not isinstance(chain, str):
        raise ValueError('Jaringan wajib berupa nama chain')
    name = ALIASES.get(chain.upper(), chain.upper())
    if name not in CHAIN_CONFIG:
        raise ValueError('Jaringan tidak didukung')
    return name


def decode_base58(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 100 or any(char not in BASE58 for char in value):
        raise ValueError('Base58 tidak valid')
    number = 0
    for char in value:
        number = number * 58 + BASE58.index(char)
    return b'\0' * (len(value) - len(value.lstrip('1'))) + (number.to_bytes((number.bit_length() + 7) // 8, 'big') if number else b'')


def _valid_bitcoin(address):
    if not isinstance(address, str):
        return False
    if address.lower().startswith('bc1'):
        if address != address.lower() and address != address.upper() or not 14 <= len(address) <= 90:
            return False
        text = address.lower()
        if any(char not in BECH32 for char in text[3:]):
            return False
        data = [BECH32.index(char) for char in text[3:]]
        polymod = 1
        for value in [3, 3, 0, 2, 3] + data:
            top = polymod >> 25
            polymod = (polymod & 0x1ffffff) << 5 ^ value
            for bit, generator in enumerate((0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)):
                if top >> bit & 1:
                    polymod ^= generator
        witness_version = data[0]
        if witness_version > 16 or polymod != (1 if witness_version == 0 else 0x2bc830a3):
            return False
        accumulator = bits = 0
        program = []
        for value in data[1:-6]:
            accumulator = (accumulator << 5) | value
            bits += 5
            while bits >= 8:
                bits -= 8
                program.append((accumulator >> bits) & 255)
        return bits < 5 and not ((accumulator << (8 - bits)) & 255) and 2 <= len(program) <= 40 and (witness_version != 0 or len(program) in {20, 32})
    try:
        raw = decode_base58(address)
        return len(raw) == 25 and raw[0] in {0, 5} and sha256(sha256(raw[:-4]).digest()).digest()[:4] == raw[-4:]
    except ValueError:
        return False


def is_valid_address(chain, address):
    try:
        name = normalize_chain(chain)
        if name == 'BTC':
            return _valid_bitcoin(address)
        if name == 'SOLANA':
            return len(decode_base58(address)) == 32
        if name == 'TRON':
            return isinstance(address, str) and bool(re.fullmatch(r'T[A-Za-z0-9]{20,99}', address))
        return isinstance(address, str) and bool(re.fullmatch(r'0x[0-9A-Fa-f]{40}', address))
    except (ValueError, TypeError):
        return False


def is_valid_tx_hash(chain, tx_hash):
    name = normalize_chain(chain)
    if name == 'SOLANA':
        try:
            return len(decode_base58(tx_hash)) == 64
        except ValueError:
            return False
    return isinstance(tx_hash, str) and bool(re.fullmatch(r'[0-9A-Fa-f]{64}' if name in {'TRON', 'BTC'} else r'0x[0-9A-Fa-f]{64}', tx_hash))


def explorer_address_url(chain, address):
    name = normalize_chain(chain)
    if not is_valid_address(name, address):
        return None
    path = '/#/address/' if name == 'TRON' else '/account/' if name == 'SOLANA' else '/address/'
    return CHAIN_CONFIG[name]['explorer_url'] + path + address


def explorer_tx_url(chain, tx_hash):
    name = normalize_chain(chain)
    if not is_valid_tx_hash(name, tx_hash):
        return None
    return CHAIN_CONFIG[name]['explorer_url'] + ('/#/transaction/' if name == 'TRON' else '/tx/') + tx_hash
