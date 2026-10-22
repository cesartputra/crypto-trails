import asyncio
import httpx
import pytest
from services import etherscan_client as module
from services.etherscan_client import EtherscanClient,KNOWN_BRIDGE_CONTRACTS,EtherscanAPIError

A='0x'+'a'*40
B='0x'+'b'*40
C='0x'+'c'*40
BRIDGE='0x5427fefa711eff984124bfbb1ab6fbf5e3da1820'
class Consumer:
    logs=[]
    async def consume_pakd_wallet_reverse_lookup_api(self,address,chain):return None


def row(sender,target,hash_char,timestamp):
    return {'from':sender,'to':target,'hash':'0x'+hash_char*64,'timeStamp':str(timestamp),'value':'1000000000000000000','isError':'0','txreceipt_status':'1'}


def test_v2_bridge_pivots_and_continues_real_destination_chain(monkeypatch):
    calls=[]
    async def no_delay():pass
    monkeypatch.setattr(module,'_throttle',no_delay)
    def handle(req):
        chain=req.url.params.get('chainid');action=req.url.params['action'];address=req.url.params['address'];calls.append((chain,action,address))
        result='0' if action=='balance' else []
        if action=='txlist' and address==A:
            if chain=='1':result=[row(A,BRIDGE,'1',1700000000)]
            if chain=='42161':result=[row(B,A,'2',1700000060),row(A,C,'3',1700000090)]
        return httpx.Response(200,json={'status':'1','message':'OK','result':result})
    engine=EtherscanClient(api_key='test-key',transport=httpx.MockTransport(handle),pakd_consumer=Consumer())
    graph=asyncio.run(engine.trace_real_wallet_on_etherscan(A,max_hops=1,token_filter='ETH',auto_scan_cross_chain=True))
    pivots=[e for e in graph['edges'] if e.edge_type=='CROSS_CHAIN_BRIDGE']
    assert pivots and all(e.metadata['correlation_status']=='CANDIDATE_UNVERIFIED' for e in pivots)
    assert pivots[0].bridged_from_chain=='ETHEREUM' and pivots[0].bridged_to_chain=='ARBITRUM'
    assert pivots[0].crypto_amount==0 and pivots[0].tx_hash is None
    assert any(n.is_bridge_node and n.bridge_protocol_name=='Celer cBridge' for n in graph['nodes'])
    assert any(e.chain=='ARBITRUM' and e.tx_hash=='0x'+'3'*64 and e.explorer_url.startswith('https://arbiscan.io/tx/') for e in graph['edges'])
    seed_nodes=[n for n in graph['nodes'] if n.address==A]
    assert len({n.id for n in seed_nodes})>=2
    assert graph['metadata']['requests_made']<=36
    assert any(chain=='42161' for chain,_,_ in calls)


def test_bridge_scenarios_use_destination_pakd_api_and_keep_governance(trace):
    for scenario,chain,pakd in [('bridge-ethereum-arbitrum','ARBITRUM','Indodax'),('bridge-ethereum-bsc','BSC','Reku')]:
        graph=trace(scenario)
        pivots=[e for e in graph['edges'] if e['edge_type']=='CROSS_CHAIN_BRIDGE']
        assert len(pivots)==1 and pivots[0]['bridged_to_chain']==chain
        assert any(n['chain']==chain and n['is_bridge_node'] is False for n in graph['nodes'])
        assert any(r['institution_name']==pakd and r['institution_category']=='PAKD' for r in graph['governance_requests'])
        assert graph['pakd_api_exchange_logs'] and float(graph['confirmed_frozen_amount_idr'])==0
        assert all(e['source'] in {n['id'] for n in graph['nodes']} and e['target'] in {n['id'] for n in graph['nodes']} for e in graph['edges'])


def test_no_key_bsc_cannot_use_ethereum_fallback(monkeypatch):
    monkeypatch.setattr(module,'_config',lambda:{'ETHERSCAN_API_KEY':'','ETHERSCAN_BASE_URL':'','ETHERSCAN_FALLBACK_EXPLORER_URL':''})
    async def no_delay():pass
    monkeypatch.setattr(module,'_throttle',no_delay)
    engine=EtherscanClient(api_key='',transport=httpx.MockTransport(lambda r:pytest.fail('BSC incorrectly sent to anotherchain')),pakd_consumer=Consumer())
    with pytest.raises(EtherscanAPIError):asyncio.run(engine.trace_real_wallet_on_etherscan(A,source_chain='BSC',max_hops=1))


def test_known_bridge_dictionary_is_chain_scoped_and_has_sources():
    assert len(KNOWN_BRIDGE_CONTRACTS['ETHEREUM'])>=8
    assert all(module.ADDRESS.fullmatch(a) and info['source_url'].startswith('https://') for a,info in KNOWN_BRIDGE_CONTRACTS['ETHEREUM'].items())


@pytest.mark.parametrize('chain,chain_id,host',[('ETHEREUM',1,'etherscan.io'),('BSC',56,'bscscan.com'),('POLYGON',137,'polygonscan.com'),('ARBITRUM',42161,'arbiscan.io'),('OPTIMISM',10,'optimistic.etherscan.io'),('BASE',8453,'basescan.org')])
def test_v2_each_chain_uses_correct_id_native_asset_and_explorer(monkeypatch,chain,chain_id,host):
    async def no_delay():pass
    monkeypatch.setattr(module,'_throttle',no_delay)
    requests=[]
    def handle(req):
        requests.append(req)
        return httpx.Response(200,json={'status':'1','result':'0' if req.url.params['action']=='balance' else [row(A,B,'8',1700000000)]})
    engine=EtherscanClient(api_key='test-key',transport=httpx.MockTransport(handle),pakd_consumer=Consumer())
    result=asyncio.run(engine.trace_real_wallet_on_etherscan(A,max_hops=1,token_filter='ETH',source_chain=chain))
    assert all(req.url.params['chainid']==str(chain_id) for req in requests)
    assert result['edges'][0].chain_id==chain_id
    assert result['edges'][0].explorer_url.startswith('https://'+host+'/tx/')
    assert all(node.chain==chain for node in result['nodes'])


@pytest.mark.parametrize('chain,host',[('ETHEREUM','eth.blockscout.com'),('POLYGON','polygon.blockscout.com'),('ARBITRUM','arbitrum.blockscout.com'),('OPTIMISM','optimism.blockscout.com'),('BASE','base.blockscout.com')])
def test_fallback_requests_are_chain_scoped_and_keyless(monkeypatch,chain,host):
    async def no_delay():pass
    monkeypatch.setattr(module,'_throttle',no_delay)
    monkeypatch.setattr(module,'_config',lambda:{'ETHERSCAN_API_KEY':'','ETHERSCAN_BASE_URL':'','ETHERSCAN_FALLBACK_EXPLORER_URL':''})
    requests=[]
    def handle(req):
        requests.append(req)
        return httpx.Response(200,json={'status':'1','result':'0' if req.url.params['action']=='balance' else []})
    engine=EtherscanClient(api_key='',transport=httpx.MockTransport(handle),pakd_consumer=Consumer())
    asyncio.run(engine.trace_real_wallet_on_etherscan(A,max_hops=1,source_chain=chain,token_filter='ETH'))
    assert all(req.url.host==host and 'apikey' not in req.url.params for req in requests)
