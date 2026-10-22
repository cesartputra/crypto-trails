"""Simulated PAKD-owned scoped SQLite stores and REST contracts; synthetic data only."""
from decimal import Decimal, InvalidOperation
import re
import sqlite3
from threading import RLock
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from scenarios import SCENARIOS
from services.chains import is_valid_address

EXCLUDED_INTERNAL_PAKD_FIELDS = ["password_hash", "api_secret", "internal_orderbook", "cold_wallet_private_key", "device_imei", "fee_tier", "private_key"]
PAKD_CODES = {"INDODAX": "Indodax", "TOKOCRYPTO": "Tokocrypto", "PINTU": "Pintu", "REKU": "Reku", "DEMO_P2P": "PAKD Demo P2P"}
SCOPED_FIELDS = {
    "ojk_scoped_fiat_inflows": ["pakd_code", "inflow_id", "va_or_account_number", "rrn_reference", "amount_idr", "inflow_timestamp", "intake_channel", "user_uid", "kyc_nik_masked", "kyc_name_masked", "remaining_fiat_balance_idr"],
    "ojk_scoped_crypto_withdrawals": ["withdrawal_id", "pakd_code", "user_uid", "linked_inflow_id", "token_symbol", "crypto_amount", "source_chain", "source_chain_id", "withdrawal_tx_hash", "initial_wallet_address", "withdrawal_timestamp", "remaining_crypto_balance", "chain", "chain_family", "scanner_provider", "fiat_value_idr"],
    "ojk_scoped_wallet_registry": ["wallet_address", "chain", "chain_id", "pakd_code", "user_uid", "kyc_nik_masked", "wallet_role", "p2p_offramp_pujk_name", "p2p_offramp_account_number"],
}
CHAIN_ALIASES = {"ETHEREUM_ERC20":"ETHEREUM", "BSC_BEP20":"BSC", "TRON_TRC20":"TRON"}
CHAIN_IDS = {"ETHEREUM":1,"BSC":56,"POLYGON":137,"ARBITRUM":42161,"OPTIMISM":10,"BASE":8453,"TRON":None,"BTC":None,"SOLANA":None}
SOURCE = "SYNTHETIC_PAKD_SCOPED_MIRROR_REST_API"


def canonical_chain(chain):
    result = CHAIN_ALIASES.get(chain, chain)
    if result not in CHAIN_IDS:
        raise ValueError("Jaringan Mirror API tidak didukung")
    return result


def normalize_address(address, chain):
    chain = canonical_chain(chain)
    if chain in {"BTC", "SOLANA"}:
        if not is_valid_address(chain, address):
            raise ValueError("Alamat BTC/Solana tidak valid")
        return address
    if chain == "TRON":
        if not isinstance(address,str) or not re.fullmatch(r"T[A-Za-z0-9]{20,99}",address):
            raise ValueError("Alamat TRON tidak valid")
        return address
    if not isinstance(address,str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}",address):
        raise ValueError("Alamat EVM tidak valid")
    return address.lower()


def pakd_code_for(name):
    return next((code for code,label in PAKD_CODES.items() if name in {code,label}), name)


def money(value):
    try:
        amount=Decimal(str(value))
        if not amount.is_finite() or not 0 <= amount <= Decimal('1000000000000') or amount != amount.quantize(Decimal('.01')):
            raise ValueError()
        return format(amount.quantize(Decimal('.01')),'f')
    except (ValueError,InvalidOperation):
        raise ValueError("Nominal IDR tidak valid") from None


def masked_name(value):
    return ' '.join(part[0]+'***' for part in value.split()[:4] if part)


class ScopedMirrorProvider:
    def __init__(self, seed=True):
        self._lock=RLock()
        self.databases={code: sqlite3.connect(':memory:',check_same_thread=False) for code in PAKD_CODES}
        for connection in self.databases.values():
            connection.row_factory=sqlite3.Row
            connection.executescript('''
            CREATE TABLE ojk_scoped_fiat_inflows (
              pakd_code TEXT NOT NULL,inflow_id TEXT PRIMARY KEY,va_or_account_number TEXT NOT NULL,
              rrn_reference TEXT NOT NULL,amount_idr TEXT NOT NULL,inflow_timestamp TEXT NOT NULL,
              intake_channel TEXT NOT NULL,user_uid TEXT NOT NULL,kyc_nik_masked TEXT NOT NULL,
              kyc_name_masked TEXT NOT NULL,remaining_fiat_balance_idr TEXT NOT NULL);
            CREATE TABLE ojk_scoped_crypto_withdrawals (
              withdrawal_id TEXT PRIMARY KEY,pakd_code TEXT NOT NULL,user_uid TEXT NOT NULL,
              linked_inflow_id TEXT NOT NULL,token_symbol TEXT NOT NULL,crypto_amount TEXT NOT NULL,
              source_chain TEXT NOT NULL,source_chain_id INTEGER,withdrawal_tx_hash TEXT NOT NULL,
              initial_wallet_address TEXT NOT NULL,withdrawal_timestamp TEXT NOT NULL,remaining_crypto_balance TEXT NOT NULL,
              chain TEXT NOT NULL,chain_family TEXT NOT NULL,scanner_provider TEXT NOT NULL,fiat_value_idr TEXT NOT NULL);
            CREATE TABLE ojk_scoped_wallet_registry (
              wallet_address TEXT NOT NULL,chain TEXT NOT NULL,chain_id INTEGER,pakd_code TEXT NOT NULL,
              user_uid TEXT NOT NULL,kyc_nik_masked TEXT NOT NULL,wallet_role TEXT NOT NULL,
              p2p_offramp_pujk_name TEXT,p2p_offramp_account_number TEXT,PRIMARY KEY(wallet_address,chain));
            CREATE INDEX scoped_inflow_lookup ON ojk_scoped_fiat_inflows(va_or_account_number,rrn_reference,amount_idr);
            ''')
        if seed:self._seed()

    def _seed(self):
        for scenario in SCENARIOS.values():
            ramp=scenario['onramp'];code=pakd_code_for(ramp['platform']);conn=self.databases[code]
            branches=scenario['chain_dispersals']
            for intake in scenario['pakd_intake_ledger']:
                conn.execute('INSERT INTO ojk_scoped_fiat_inflows VALUES (?,?,?,?,?,?,?,?,?,?,?)',(
                    code,intake['intake_id'],intake['gateway_account'],intake['rrn_reference_number'],money(intake['fiat_amount_idr']),
                    intake['timestamp'],intake['intake_mechanism'],ramp['uid'],ramp['kyc']['nik_masked'],masked_name(ramp['kyc']['name']),money(ramp.get('held_escrow_idr','0'))))
                for branch in branches:
                    chain=canonical_chain(branch['chain'])
                    remaining=str(Decimal(ramp.get('held_escrow_idr','0'))/Decimal(16000)) if len(branches)==1 else '0'
                    conn.execute('INSERT INTO ojk_scoped_crypto_withdrawals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(
                        branch['withdrawal_id'],code,ramp['uid'],intake['intake_id'],branch['token_symbol'],branch['crypto_amount'],chain,CHAIN_IDS[chain],
                        branch['withdrawal_tx_hash'],normalize_address(branch['initial_wallet_address'],chain),branch['withdrawal_timestamp'],remaining,
                        chain,branch['chain_family'],branch['scanner_provider'],money(branch['fiat_value_idr'])))
                    self.register_wallet(branch['initial_wallet_address'],chain,code,ramp['uid'],ramp['kyc']['nik_masked'],'REGISTERED_WITHDRAWAL_WHITELIST')
            for wallet in scenario['wallets']:
                if wallet.get('recovery_institution')!='PAKD':continue
                target_code=pakd_code_for(wallet['platform']);target_chain=canonical_chain(wallet.get('chain',ramp['chain']))
                off=next((order for order in scenario['p2p_orders'] if order['p2p_direction']=='OFF_RAMP_WITHDRAWAL' and order.get('exchange_node')==wallet['id']),None)
                pujkn=next((a['bank'] for a in scenario['accounts'] if off and a['account']==off.get('linked_final_bank_account')),None)
                account=off.get('linked_final_bank_account') if off else None
                if scenario.get('multi_chain_dispersal') and wallet['id']=='sol-deposit':
                    pujkn,account='BRI',next(a['account'] for a in scenario['accounts'] if a['id']=='final-sol-bank')
                self.register_wallet(wallet['address'],target_chain,target_code,wallet['uid'],wallet.get('kyc_nik_masked',ramp['kyc']['nik_masked']),
                    'CUSTODIAL_DEPOSIT_ADDRESS',pujkn,account)
        for conn in self.databases.values():conn.commit()

    def register_wallet(self,address,chain,pakd_code,user_uid,nik_masked,wallet_role,pujk=None,account=None):
        chain=canonical_chain(chain);address=normalize_address(address,chain)
        if pakd_code not in self.databases or not user_uid or not isinstance(user_uid,str) or len(user_uid)>120:
            raise ValueError('PAKD/UID tidak valid')
        if not re.fullmatch(r'[0-9]{4}\*{8}[0-9]{4}',nik_masked or '') or wallet_role not in {'CUSTODIAL_DEPOSIT_ADDRESS','REGISTERED_WITHDRAWAL_WHITELIST'}:
            raise ValueError('Profil scoped wallet tidak valid')
        values=(address,chain,CHAIN_IDS[chain],pakd_code,user_uid,nik_masked,wallet_role,pujk,account)
        with self._lock,self.databases[pakd_code]:
            existing=self.databases[pakd_code].execute('SELECT * FROM ojk_scoped_wallet_registry WHERE wallet_address=? AND chain=?',(address,chain)).fetchone()
            if existing and tuple(existing)!=values:raise ValueError('Wallet sudah dipetakan ke profil lain')
            self.databases[pakd_code].execute('INSERT OR IGNORE INTO ojk_scoped_wallet_registry VALUES (?,?,?,?,?,?,?,?,?)',values)

    def connection(self,code):
        if code not in self.databases:raise ValueError('PAKD code tidak dikenal')
        return self.databases[code]

    def lookup_inflow(self,code,va_number,rrn_reference,amount_idr):
        conn=self.connection(code)
        with self._lock:
            inflows=conn.execute('SELECT * FROM ojk_scoped_fiat_inflows WHERE pakd_code=? AND va_or_account_number=? AND rrn_reference=? AND amount_idr=? LIMIT 2',(code,va_number,rrn_reference,money(amount_idr))).fetchall()
            if len(inflows)>1:raise ValueError('Pencocokan scoped inflow ambigu')
            if not inflows:return {'inflow':None,'withdrawals':[],'chains_used':[]}
            inflow=dict(inflows[0])
            withdrawals=conn.execute('SELECT * FROM ojk_scoped_crypto_withdrawals WHERE pakd_code=? AND user_uid=? AND linked_inflow_id=? ORDER BY withdrawal_timestamp,withdrawal_id LIMIT 17',(code,inflow['user_uid'],inflow['inflow_id'])).fetchall()
            if len(withdrawals)>16:raise ValueError('Maksimum 16 withdrawal per inflow pada demo')
        return {'inflow':self._scope('ojk_scoped_fiat_inflows',inflow),'chains_used':list(dict.fromkeys(row['chain'] for row in withdrawals)),
                'withdrawals':[self._scope('ojk_scoped_crypto_withdrawals',dict(row)) for row in withdrawals]}

    def lookup_wallet(self,code,address,chain):
        chain=canonical_chain(chain); address=normalize_address(address,chain)
        with self._lock:
            row=self.connection(code).execute('SELECT * FROM ojk_scoped_wallet_registry WHERE pakd_code=? AND wallet_address=? AND chain=?',(code,address,chain)).fetchone()
        return self._scope('ojk_scoped_wallet_registry',dict(row)) if row else None

    @staticmethod
    def _scope(table,row):
        return {field:row[field] for field in SCOPED_FIELDS[table]}

    def preview(self):
        tables={table:[] for table in SCOPED_FIELDS}
        with self._lock:
            for conn in self.databases.values():
                for table in tables:
                    tables[table].extend(self._scope(table,dict(row)) for row in conn.execute(f'SELECT * FROM {table} LIMIT 20'))
        return {**metadata(),'tables':tables,'scoped_schema':SCOPED_FIELDS,'row_counts':{table:len(rows) for table,rows in tables.items()},'preview_limit':100}


def metadata():
    return {'simulation':True,'evidence_source':SOURCE,'transport_mode':'SIMULATED_PAKD_ASGI_HTTP',
            'excluded_internal_pakd_fields':EXCLUDED_INTERNAL_PAKD_FIELDS,
            'privacy_note':'Data sintetis; scoped schema adalah desain minimisasi data demo, bukan sertifikasi kepatuhan UU PDP.'}


class InflowLookup(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    va_number:str=Field(min_length=3,max_length=64)
    rrn_reference:str=Field(min_length=6,max_length=80)
    amount_idr:Decimal=Field(ge=0,le=Decimal('1000000000000'),max_digits=15,decimal_places=2,allow_inf_nan=False)


class WalletLookup(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    wallet_address:str=Field(min_length=20,max_length=100)
    chain:str=Field(default='ETHEREUM',max_length=40)


provider=ScopedMirrorProvider()
router=APIRouter(prefix='/api/v1/pakd-mirror-api',tags=['PAKD-owned Scoped Mirror REST API (simulation)'])


@router.get('/records')
async def scoped_schema_preview():
    return provider.preview()


@router.post('/{pakd_code}/inflow-lookup')
async def inflow_lookup(pakd_code:str,payload:InflowLookup):
    try:
        return {**metadata(),'pakd_code':pakd_code,**provider.lookup_inflow(pakd_code,payload.va_number,payload.rrn_reference,payload.amount_idr)}
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@router.post('/{pakd_code}/wallet-lookup')
async def wallet_lookup(pakd_code:str,payload:WalletLookup):
    try:return {**metadata(),'pakd_code':pakd_code,'wallet':provider.lookup_wallet(pakd_code,payload.wallet_address,payload.chain)}
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


@router.post('/{pakd_code}/p2p-lookup')
async def p2p_lookup(pakd_code:str,payload:WalletLookup):
    try:
        wallet=provider.lookup_wallet(pakd_code,payload.wallet_address,payload.chain)
        return {**metadata(),'pakd_code':pakd_code,'wallet':wallet if wallet and wallet['p2p_offramp_account_number'] else None}
    except ValueError as exc:raise HTTPException(422,str(exc)) from None


provider_app=FastAPI(docs_url=None,redoc_url=None,title='Simulated PAKD Scoped Mirror API')
provider_app.include_router(router)
