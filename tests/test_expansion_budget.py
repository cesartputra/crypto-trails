"""Concurrent manual expansions must not spend the same remaining trace budget."""
import asyncio

import httpx

from schemas import CrossRailGraphResponse, TrailNode
from services import cryptoapis_continuation
from services.multichain_scanner import MultichainScanner


def test_concurrent_expansions_share_one_budget_and_release_trace_lock(monkeypatch):
    import main
    wallets = ['0x' + f'{number:040x}' for number in range(21, 24)]
    graph = CrossRailGraphResponse(trace_id='concurrent-free-budget', iasc_ticket_id='LIVE', fna_trail_id='',
        scenario_id=None, simulation=False, nodes=[TrailNode(id=str(index), label='Frontier',
            node_zone='ONCHAIN_WALLET', node_type='LIVE_UNATTRIBUTED_WALLET', chain='ETHEREUM',
            address=address, hop=3, metadata={'wallet_hop': 3}) for index, address in enumerate(wallets[:2])],
        edges=[], total_scam_idr=0, total_traced_idr=0, recoverable_amount_idr=0, recovery_probability_pct=0,
        live_metadata={'provider': 'ETHERSCAN', 'token_filter': 'ETH', 'requests_made': 12,
            'cryptoapis_requests_made': 5, 'cryptoapis_credits_consumed': 710})
    main.store_trace(graph)
    monkeypatch.setattr(cryptoapis_continuation, 'validate_config', lambda: {
        'CRYPTOAPIS_API_KEY': 'fixture-key', 'CRYPTOAPIS_BASE_URL': 'https://rest.cryptoapis.io'})
    calls = []

    async def run():
        entered, release = asyncio.Event(), asyncio.Event()

        async def handler(request):
            calls.append(request)
            entered.set()
            if len(calls) > 1:
                release.set()
            await release.wait()
            address = request.url.path.split('/')[-2]
            return httpx.Response(200, headers={'x-credits-consumed': '142'}, json={'data': {
                'hasMore': False, 'items': [{'sender': address, 'recipient': wallets[2],
                    'hash': '0x' + 'a' * 64, 'timestamp': 1700000000, 'status': '0x1',
                    'value': {'amount': '1', 'unit': 'ETH'}}]}})

        monkeypatch.setattr(main, 'MultichainScanner', lambda: MultichainScanner(transport=httpx.MockTransport(handler)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
            params = {'trace_id': graph.trace_id, 'chain': 'ETHEREUM', 'live': 'true'}
            first = asyncio.create_task(client.get(f'/api/v1/trail/address/{wallets[0]}/expand', params=params))
            await entered.wait()
            second = await client.get(f'/api/v1/trail/address/{wallets[1]}/expand', params=params)
            release.set()
            completed = await first
            assert second.status_code == 409
            assert completed.status_code == 200 and len(calls) == 1
            assert graph.live_metadata['cryptoapis_requests_made'] == 6
            assert graph.live_metadata['cryptoapis_credits_consumed'] == 852
            assert graph.live_metadata['requests_made'] == 13
            retry = await client.get(f'/api/v1/trail/address/{wallets[1]}/expand', params=params)
            assert retry.status_code == 200 and len(calls) == 1
            assert retry.json()['metadata']['continuation_status'] == 'BUDGET_LIMIT'
            assert graph.live_metadata['cryptoapis_credits_consumed'] == 852

    asyncio.run(run())
