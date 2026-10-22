import asyncio
from decimal import Decimal
from pathlib import Path
import httpx
import pytest
from services.pakd_api_consumer import PAKDAPIConsumer, PAKDAPIError, FIELDS, _safe_payload
from services.pakd_scoped_mirror_provider import ScopedMirrorProvider, SCOPED_FIELDS, EXCLUDED_INTERNAL_PAKD_FIELDS
from scenarios import SCENARIOS


def test_provider_schema_contains_only_requested_columns_and_masks_names():
    db=ScopedMirrorProvider()
    preview=db.preview()
    assert set(preview['tables'])==set(SCOPED_FIELDS)
    for table,rows in preview['tables'].items():
        assert rows
        assert all(set(row)==set(SCOPED_FIELDS[table]) for row in rows)
        assert all(not set(row)&set(EXCLUDED_INTERNAL_PAKD_FIELDS) for row in rows)
    assert all('***' in row['kyc_name_masked'] for row in preview['tables']['ojk_scoped_fiat_inflows'])


@pytest.mark.parametrize('scenario_id',list(SCENARIOS))
def test_consumer_uses_http_forward_reverse_and_scoped_logs(scenario_id):
    scenario=SCENARIOS[scenario_id];handoff=scenario['handoff']
    async def run():
        consumer=PAKDAPIConsumer()
        result=await consumer.consume_pakd_inflow_lookup_api(handoff['pakd_gateway_va_or_account'],handoff['rrn_reference_number'],handoff['fiat_transfer_amount_idr'])
        assert result['user_uid']==scenario['onramp']['uid']
        assert result['withdrawal_tx_hash']==scenario['onramp']['withdrawal_tx_hash']
        wallet=await consumer.consume_pakd_wallet_reverse_lookup_api(result['initial_wallet_address'],result['source_chain'])
        assert wallet['user_uid']==result['user_uid']
        assert consumer.logs and all(log['response_status_code']==200 for log in consumer.logs)
        assert all(log['endpoint_url'].startswith('http://pakd-mirror.local/api/') for log in consumer.logs)
        assert all(log['latency_ms']>=0 for log in consumer.logs)
        for log in consumer.logs:
            assert set(log['scoped_fields_returned'])<=set(field for group in FIELDS.values() for field in group)
    asyncio.run(run())


def test_consumer_does_not_open_database_and_filters_internal_response_fields():
    source=Path('services/pakd_api_consumer.py')
    if not source.exists():source=Path(__file__).parents[1]/source
    assert 'sqlite3' not in source.read_text()
    row={field:'x' for field in FIELDS['ojk_scoped_wallet_registry']}
    row.update(chain_id=1,password_hash='TOP-SECRET',cold_wallet_private_key='PRIVATE-KEY')
    result=_safe_payload({'wallet':row,'api_secret':'TOP-SECRET'})
    assert 'TOP-SECRET' not in repr(result) and 'PRIVATE-KEY' not in repr(result)
    row['user_uid']={'password_hash':'HIDDEN-SECRET'}
    with pytest.raises(PAKDAPIError):_safe_payload({'wallet':row})


def test_provider_rest_routes_are_documented_and_pakd_scoped(client):
    schema=client.get('/openapi.json').json()
    path='/api/v1/pakd-mirror-api/{pakd_code}/inflow-lookup'
    assert path in schema['paths']
    s=SCENARIOS['direct-va-loopback']['handoff']
    payload={'va_number':s['pakd_gateway_va_or_account'],'rrn_reference':s['rrn_reference_number'],'amount_idr':s['fiat_transfer_amount_idr']}
    response=client.post('/api/v1/pakd-mirror-api/TOKOCRYPTO/inflow-lookup',json=payload)
    assert response.status_code==200 and response.json()['inflow']['pakd_code']=='TOKOCRYPTO'
    assert client.post('/api/v1/pakd-mirror-api/INDODAX/inflow-lookup',json=payload).json()['inflow'] is None
    payload['rrn_reference']="' OR 1=1 --"
    assert client.post('/api/v1/pakd-mirror-api/TOKOCRYPTO/inflow-lookup',json=payload).json()['inflow'] is None


def test_reverse_registry_chain_isolation():
    async def run():
        c=PAKDAPIConsumer()
        a='0x7400000000000000000000000000000000000004'
        assert (await c.consume_pakd_wallet_reverse_lookup_api(a,'ETHEREUM'))['pakd_name']=='Indodax'
        assert await c.consume_pakd_wallet_reverse_lookup_api(a,'BSC') is None
    asyncio.run(run())


def test_provider_http_failure_is_safe_and_logged():
    c=PAKDAPIConsumer(transport=httpx.MockTransport(lambda req:httpx.Response(500,text='sensitive-upstream-secret')))
    with pytest.raises(PAKDAPIError) as error:asyncio.run(c.consume_pakd_wallet_reverse_lookup_api('0x'+'a'*40))
    assert 'sensitive-upstream-secret' not in repr(c.logs)+str(error.value)
    assert c.logs[0]['response_status_code']==500
