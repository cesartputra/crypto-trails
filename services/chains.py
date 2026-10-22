"""Chain-scoped public explorer configuration; TRON is a demo-only network."""
import re

CHAIN_CONFIG = {
    'ETHEREUM': {'chain_id': 1, 'native_symbol': 'ETH', 'explorer_url': 'https://etherscan.io', 'fallback_url': 'https://eth.blockscout.com/api'},
    'BSC': {'chain_id': 56, 'native_symbol': 'BNB', 'explorer_url': 'https://bscscan.com', 'fallback_url': None},
    'POLYGON': {'chain_id': 137, 'native_symbol': 'POL', 'explorer_url': 'https://polygonscan.com', 'fallback_url': 'https://polygon.blockscout.com/api'},
    'ARBITRUM': {'chain_id': 42161, 'native_symbol': 'ETH', 'explorer_url': 'https://arbiscan.io', 'fallback_url': 'https://arbitrum.blockscout.com/api'},
    'OPTIMISM': {'chain_id': 10, 'native_symbol': 'ETH', 'explorer_url': 'https://optimistic.etherscan.io', 'fallback_url': 'https://optimism.blockscout.com/api'},
    'BASE': {'chain_id': 8453, 'native_symbol': 'ETH', 'explorer_url': 'https://basescan.org', 'fallback_url': 'https://base.blockscout.com/api'},
    'TRON': {'chain_id': None, 'native_symbol': 'TRX', 'explorer_url': 'https://tronscan.org', 'fallback_url': None},
}
ALIASES = {'ETHEREUM_ERC20': 'ETHEREUM', 'BSC_BEP20': 'BSC', 'TRON_TRC20': 'TRON'}


def normalize_chain(chain):
    if not isinstance(chain, str):
        raise ValueError('Jaringan wajib berupa nama chain')
    name = ALIASES.get(chain.upper(), chain.upper())
    if name not in CHAIN_CONFIG:
        raise ValueError('Jaringan tidak didukung')
    return name


def explorer_address_url(chain, address):
    name = normalize_chain(chain)
    if not isinstance(address, str):
        return None
    if name == 'TRON':
        return CHAIN_CONFIG[name]['explorer_url'] + '/#/address/' + address if re.fullmatch(r'T[A-Za-z0-9]{20,99}', address) else None
    return CHAIN_CONFIG[name]['explorer_url'] + '/address/' + address if re.fullmatch(r'0x[0-9A-Fa-f]{40}', address) else None


def explorer_tx_url(chain, tx_hash):
    name = normalize_chain(chain)
    if not isinstance(tx_hash, str):
        return None
    if name == 'TRON':
        return CHAIN_CONFIG[name]['explorer_url'] + '/#/transaction/' + tx_hash if re.fullmatch(r'[0-9A-Fa-f]{64}', tx_hash) else None
    return CHAIN_CONFIG[name]['explorer_url'] + '/tx/' + tx_hash if re.fullmatch(r'0x[0-9A-Fa-f]{64}', tx_hash) else None
