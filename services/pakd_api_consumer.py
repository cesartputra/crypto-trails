"""OJK-side REST consumer; no SQL connection or database handle crosses this boundary."""
import time
from decimal import Decimal
import httpx
from services.chains import CHAIN_CONFIG, normalize_chain

PAKD_CODES={'INDODAX':'Indodax','TOKOCRYPTO':'Tokocrypto','PINTU':'Pintu','REKU':'Reku','DEMO_P2P':'PAKD Demo P2P'}
SOURCE='SYNTHETIC_PAKD_SCOPED_MIRROR_REST_API'
FIELDS={
 'ojk_scoped_fiat_inflows':('pakd_code','inflow_id','va_or_account_number','rrn_reference','amount_idr','inflow_timestamp','intake_channel','user_uid','kyc_nik_masked','kyc_name_masked','remaining_fiat_balance_idr'),
 'ojk_scoped_crypto_withdrawals':('withdrawal_id','pakd_code','user_uid','linked_inflow_id','token_symbol','crypto_amount','source_chain','source_chain_id','withdrawal_tx_hash','initial_wallet_address','withdrawal_timestamp','remaining_crypto_balance','chain','chain_family','scanner_provider','fiat_value_idr'),
 'ojk_scoped_wallet_registry':('wallet_address','chain','chain_id','pakd_code','user_uid','kyc_nik_masked','wallet_role','p2p_offramp_pujk_name','p2p_offramp_account_number')}
EXCLUDED=('password_hash','api_secret','internal_orderbook','cold_wallet_private_key','device_imei','fee_tier','private_key')
ALIASES={'ETHEREUM_ERC20':'ETHEREUM','BSC_BEP20':'BSC','TRON_TRC20':'TRON'}


class PAKDAPIError(RuntimeError):
    pass


def _record(table,value):
    if value is None:return None
    if not isinstance(value,dict) or not set(FIELDS[table])<=value.keys():
        raise PAKDAPIError('Respons PAKD Mirror API tidak sesuai scoped contract')
    if any(not isinstance(value[key], (str, int, type(None))) or isinstance(value[key], bool) for key in FIELDS[table]):
        raise PAKDAPIError("Nilai field scoped wajib scalar")
    return {key:value[key] for key in FIELDS[table]}


def _safe_payload(payload):
    if not isinstance(payload,dict):raise PAKDAPIError('Respons PAKD Mirror API tidak valid')
    result={'simulation':True,'evidence_source':SOURCE,'excluded_internal_pakd_fields':list(EXCLUDED),
            'transport_mode':'IN_PROCESS_ASGI_HTTP_DEMO'}
    if 'inflow' in payload:
        result['inflow']=_record('ojk_scoped_fiat_inflows',payload['inflow'])
        rows=payload.get('withdrawals',[])
        if not isinstance(rows,list) or len(rows)>16:raise PAKDAPIError('Batas scoped withdrawals tidak valid')
        result['withdrawals']=[_record('ojk_scoped_crypto_withdrawals',row) for row in rows]
        result['chains_used']=list(dict.fromkeys(row['chain'] for row in result['withdrawals']))
        if payload.get('chains_used') != result['chains_used']:
            raise PAKDAPIError('Daftar chain tidak sesuai withdrawal scoped')
    if 'wallet' in payload:result['wallet']=_record('ojk_scoped_wallet_registry',payload['wallet'])
    if 'tables' in payload:
        if not isinstance(payload['tables'],dict):raise PAKDAPIError('Scoped schema tidak valid')
        result['tables']={}
        for table in FIELDS:
            rows=payload['tables'].get(table,[])
            if not isinstance(rows,list) or len(rows)>100:raise PAKDAPIError('Batas preview Mirror API terlampaui')
            result['tables'][table]=[_record(table,row) for row in rows]
        result.update(included_scoped_fields={key:list(fields) for key,fields in FIELDS.items()},scoped_schema={key:list(fields) for key,fields in FIELDS.items()},row_counts={key:len(rows) for key,rows in result['tables'].items()},preview_limit=100,
            warning='Scoped schema dan data sintetis demo; bukan sertifikasi kepatuhan UU PDP atau API PAKD produksi.')
    return result


def _returned_fields(payload):
    fields=set()
    def visit(value):
        if isinstance(value,dict):
            for key,item in value.items():
                if key in {field for group in FIELDS.values() for field in group}:fields.add(key)
                visit(item)
        elif isinstance(value,list):
            for item in value:visit(item)
    visit({key:value for key,value in payload.items() if key in {'inflow','withdrawals','wallet','tables'}})
    return sorted(fields)


class PAKDAPIConsumer:
    def __init__(self,transport=None):
        if transport is None:
            # shortcut: demo uses real HTTP semantics in-process; replace transport with authenticated PAKD services for production.
            from services.pakd_scoped_mirror_provider import provider_app
            transport=httpx.ASGITransport(app=provider_app)
        self.transport=transport
        self.logs=[]
        self._wallet_cache={}

    async def _request(self,code,action,payload=None):
        path='/api/v1/pakd-mirror-api/'+('records' if action=='records' else code+'/'+action)
        endpoint='http://pakd-mirror.local'+path
        started=time.perf_counter();status=502;safe={'error':'PAKD Mirror API gagal diakses'}
        try:
            async with httpx.AsyncClient(transport=self.transport,base_url='http://pakd-mirror.local',timeout=5,follow_redirects=False) as client:
                response=await client.get(path) if payload is None else await client.post(path,json=payload)
                status=response.status_code
                response.raise_for_status()
                safe=_safe_payload(response.json())
            return safe
        except (httpx.HTTPError,ValueError,TypeError):
            raise PAKDAPIError('PAKD Mirror API gagal diakses atau scoped response tidak valid') from None
        finally:
            self.logs.append({'endpoint_url':endpoint,'request_payload':payload or {},'response_status_code':status,
                'latency_ms':round((time.perf_counter()-started)*1000,3),'scoped_fields_returned':_returned_fields(safe),
                'response_payload':safe,'pakd_code':code,'transport_mode':'IN_PROCESS_ASGI_HTTP_DEMO'})

    @staticmethod
    def _codes(code):
        if code is None:return list(PAKD_CODES)
        code=next((key for key,value in PAKD_CODES.items() if code in {key,value}),code)
        if code not in PAKD_CODES:raise ValueError('PAKD code tidak dikenal')
        return [code]

    async def consume_pakd_inflow_lookup_api(self,va_number,rrn_reference,amount_idr,pakd_code=None):
        matches=[]
        for code in self._codes(pakd_code):
            result=await self._request(code,'inflow-lookup',{'va_number':va_number,'rrn_reference':rrn_reference,'amount_idr':str(amount_idr)})
            inflow=result.get('inflow');withdrawals=result.get('withdrawals',[])
            if not inflow:continue
            if inflow['va_or_account_number'] != va_number or inflow['rrn_reference'] != rrn_reference or Decimal(inflow['amount_idr']) != Decimal(str(amount_idr)):
                raise PAKDAPIError('Respons inflow tidak sesuai request')
            if inflow['pakd_code']!=code or len({row['withdrawal_id'] for row in withdrawals}) != len(withdrawals):
                raise PAKDAPIError('Pemetaan PAKD atau ID withdrawal tidak valid')
            fiat_total=Decimal(0)
            families={'BTC':'BITCOIN','SOLANA':'SOLANA','TRON':'TRON'}
            for withdrawal in withdrawals:
                if withdrawal['pakd_code']!=code or withdrawal['user_uid']!=inflow['user_uid'] or withdrawal['linked_inflow_id']!=inflow['inflow_id']:
                    raise PAKDAPIError('Withdrawal tidak terikat pada inflow dan UID yang sama')
                if (withdrawal['chain'] not in CHAIN_CONFIG or withdrawal['chain']!=withdrawal['source_chain']
                    or withdrawal['chain_family'] != families.get(withdrawal['chain'],'EVM')
                    or withdrawal['source_chain_id'] != CHAIN_CONFIG[withdrawal['chain']]['chain_id']):
                    raise PAKDAPIError('Chain/family withdrawal scoped tidak konsisten')
                try:
                    fiat,crypto=Decimal(withdrawal['fiat_value_idr']),Decimal(withdrawal['crypto_amount'])
                    if not fiat.is_finite() or not crypto.is_finite() or fiat<0 or crypto<=0:raise ValueError()
                except (ValueError,ArithmeticError):raise PAKDAPIError('Nominal withdrawal scoped tidak valid') from None
                fiat_total+=fiat
            if fiat_total>Decimal(inflow['amount_idr']):raise PAKDAPIError('Total withdrawal melebihi inflow Rupiah')
            single=withdrawals[0] if len(withdrawals)==1 else {}
            matches.append({**inflow,**single,'pakd_name':PAKD_CODES[code],'deposit_id':inflow['inflow_id'],
                'deposit_timestamp':inflow['inflow_timestamp'],'intake_type':inflow['intake_channel'],'nik_masked':inflow['kyc_nik_masked'],
                'full_name':inflow['kyc_name_masked'],'remaining_pakd_balance_idr':inflow['remaining_fiat_balance_idr'],
                'chain':single.get('source_chain'),'withdrawal_tx_hash':single.get('withdrawal_tx_hash'),
                'initial_wallet_address':single.get('initial_wallet_address'),'evidence_source':SOURCE,'simulation':True,
                'chains_used':result['chains_used'],'withdrawals':withdrawals,
                'scoped_inflow':inflow,'scoped_withdrawal':single,'api_endpoint':'/api/v1/pakd-mirror-api/'+code+'/inflow-lookup'})
        if len(matches)>1:raise PAKDAPIError('Pencocokan inflow lintas PAKD ambigu; review manual diperlukan')
        return matches[0] if matches else None

    async def consume_pakd_wallet_reverse_lookup_api(self,wallet_address,chain='ETHEREUM',pakd_code=None):
        chain=normalize_chain(chain)
        key=(wallet_address if chain in {'TRON','BTC','SOLANA'} else wallet_address.lower(),chain,pakd_code)
        if key in self._wallet_cache:return self._wallet_cache[key]
        matches=[]
        for code in self._codes(pakd_code):
            result=await self._request(code,'wallet-lookup',{'wallet_address':wallet_address,'chain':chain})
            wallet=result.get('wallet')
            if wallet:
                if wallet['pakd_code']!=code or wallet['chain']!=chain or (wallet['wallet_address'] if chain in {'TRON','BTC','SOLANA'} else wallet['wallet_address'].lower()) != key[0]:
                    raise PAKDAPIError('Scoped wallet memiliki konteks PAKD/chain tidak sesuai')
                matches.append({**wallet,'pakd_name':PAKD_CODES[code],'nik_masked':wallet['kyc_nik_masked'],'wallet_type':wallet['wallet_role'],
                    'evidence_source':SOURCE,'simulation':True,'scoped_wallet':wallet,'identity_note':'Kecocokan API dengan identitas sintetis; bukan KYC pemilik live terverifikasi.'})
        if len(matches)>1:raise PAKDAPIError('Kepemilikan wallet lintas PAKD ambigu')
        found=matches[0] if matches else None
        self._wallet_cache[key]=found
        return found

    async def consume_pakd_p2p_lookup_api(self,wallet_address,chain,pakd_code=None):
        chain=normalize_chain(chain)
        matches=[]
        for code in self._codes(pakd_code):
            response=await self._request(code,'p2p-lookup',{'wallet_address':wallet_address,'chain':ALIASES.get(chain,chain)})
            wallet=response.get('wallet')
            if wallet:
                returned_address=wallet['wallet_address'] if chain in {'TRON','BTC','SOLANA'} else wallet['wallet_address'].lower()
                expected_address=wallet_address if chain in {'TRON','BTC','SOLANA'} else wallet_address.lower()
                if (wallet['pakd_code']!=code or wallet['chain']!=chain or returned_address!=expected_address
                    or wallet['wallet_role']!='CUSTODIAL_DEPOSIT_ADDRESS' or not wallet['p2p_offramp_account_number']):
                    raise PAKDAPIError('Scoped P2P memiliki konteks wallet/PAKD tidak sesuai')
                matches.append(wallet)
        if len(matches)>1:raise PAKDAPIError('P2P off-ramp lintas PAKD ambigu')
        return matches[0] if matches else None

    async def get_schema_preview_api(self):
        return await self._request('ALL','records')
