from decimal import Decimal

import pytest

from scenarios import SCENARIOS, get_scenario
from schemas import FNAMoneyTrailHandoffInput
from services.bank_trailer import FNAHandoffAdapter
from services.pakd_mirror_db import PakdMirrorDB
from services.p2p_and_pakd_bridge import P2PAndPAKDBridge
from services.scenario_trailer import ScenarioTrailer


@pytest.fixture
def mirror():
    db = PakdMirrorDB()
    yield db
    db.connection.close()


@pytest.mark.parametrize("scenario_id", list(SCENARIOS))
def test_sql_forward_resolution_finds_exact_profile_and_withdrawal(mirror, scenario_id):
    scenario = get_scenario(scenario_id)
    handoff = scenario["handoff"]
    found = mirror.lookup_deposit_by_fna_handoff(handoff["pakd_gateway_va_or_account"],
        handoff["rrn_reference_number"], Decimal(handoff["fiat_transfer_amount_idr"]))
    assert found["user_uid"] == scenario["onramp"]["uid"]
    assert found["nik_masked"] == scenario["onramp"]["kyc"]["nik_masked"]
    assert found["withdrawal_tx_hash"] == scenario["onramp"]["withdrawal_tx_hash"]
    assert found["initial_wallet_address"].lower() == scenario["wallets"][0]["address"].lower()
    assert found["simulation"] is True
    assert Decimal(found["remaining_pakd_balance_idr"]) >= 0
    assert mirror.lookup_deposit_by_fna_handoff(handoff["pakd_gateway_va_or_account"], handoff["rrn_reference_number"], "1") is None
    assert mirror.lookup_deposit_by_fna_handoff("' OR 1=1 --", handoff["rrn_reference_number"], handoff["fiat_transfer_amount_idr"]) is None


def test_sql_reverse_match_is_case_insensitive_and_chain_scoped(mirror):
    address = "0x7400000000000000000000000000000000000004"
    result = mirror.match_wallet_in_mirror_db(address)
    assert result["pakd_name"] == "Indodax"
    assert result["user_uid"] == "IDX-DEMO-099"
    assert result["wallet_type"] == "CUSTODIAL_DEPOSIT_ADDRESS"
    assert result["nik_masked"] == "3273********0099"
    assert mirror.match_wallet_in_mirror_db(address, "BSC_BEP20") is None
    initial = "0x71C0000000000000000000000000000000000001"
    assert mirror.match_wallet_in_mirror_db(initial)["user_uid"] == "TKC-DEMO-077"
    assert mirror.match_wallet_in_mirror_db(initial.lower())["user_uid"] == "TKC-DEMO-077"
    assert mirror.match_wallet_in_mirror_db("0x" + "f" * 40) is None


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0.001", "1000000000001"])
def test_mirror_rejects_invalid_money(mirror, amount):
    with pytest.raises(ValueError):
        mirror.lookup_deposit_by_fna_handoff("880800007500", "RRN-DEMO-0002", amount)


def test_mirror_add_wallet_requires_existing_user_and_rejects_reassignment(mirror):
    address = "0x1234567890ABCDEF1234567890ABCDEF12345678"
    result = mirror.add_wallet(address, "ETHEREUM_ERC20", "Pintu", "PTU-DEMO-010")
    assert result["pakd_name"] == "Pintu"
    assert mirror.match_wallet_in_mirror_db(address.lower())["user_uid"] == "PTU-DEMO-010"
    with pytest.raises(ValueError):
        mirror.add_wallet(address, "ETHEREUM_ERC20", "Reku", "RKU-DEMO-011")
    with pytest.raises(ValueError):
        mirror.add_wallet("0x" + "a" * 40, "ETHEREUM_ERC20", "Pintu", "MISSING-USER")
    with pytest.raises(ValueError):
        mirror.add_wallet("0x" + "b" * 40, "ETHEREUM_ERC20", "Pintu", "PTU-DEMO-010", "ARBITRARY_TYPE")
    with pytest.raises(ValueError):
        mirror.match_wallet_in_mirror_db("bad-address")


def test_ambiguous_deposit_does_not_guess_identity(mirror):
    mirror.connection.execute("""INSERT INTO mirror_pakd_fiat_deposits
        SELECT 'duplicate',pakd_name,user_uid,va_or_account_number,rrn_reference,amount_idr,deposit_timestamp,intake_type
        FROM mirror_pakd_fiat_deposits WHERE deposit_id='PAKD-IN-0002'""")
    with pytest.raises(ValueError, match="ambigu"):
        mirror.lookup_deposit_by_fna_handoff("880800007500", "RRN-DEMO-0002", "120000000")


def test_preview_exposes_actual_sqlite_tables_and_marks_synthetic(mirror):
    preview = mirror.get_mirror_db_preview()
    assert preview["simulation"] is True
    assert set(preview["tables"]) == {"mirror_pakd_users", "mirror_pakd_fiat_deposits", "mirror_pakd_wallets", "mirror_pakd_withdrawals"}
    assert preview["row_counts"]["mirror_pakd_fiat_deposits"] == 4
    assert {u["pakd_name"] for u in preview["tables"]["mirror_pakd_users"]} >= {"Tokocrypto", "Indodax", "Pintu", "Reku"}
    assert all("********" in u["nik_masked"] for u in preview["tables"]["mirror_pakd_users"])
    assert all(len(rows) <= preview["preview_limit"] for rows in preview["tables"].values())


def test_sql_bridge_and_reverse_loopback_are_used_by_scenario(mirror):
    scenario = get_scenario("direct-va-loopback")
    handoff = FNAMoneyTrailHandoffInput(**scenario["handoff"])
    nodes, edges = FNAHandoffAdapter().import_snapshot(handoff, scenario)
    bridge = P2PAndPAKDBridge(database=mirror)
    _, records = bridge.resolve_onramp(handoff, scenario, nodes, edges)
    assert records[0].metadata["mirror_deposit"]["user_uid"] == "TKC-DEMO-077"
    ScenarioTrailer().trace_fixture(scenario, nodes, edges)
    _, reverse = bridge.resolve_offramp(scenario, nodes, edges)
    assert reverse[0].pakd_name == "Indodax"
    receiver = next(n for n in nodes if n.id == "pakd-account-deposit")
    assert receiver.metadata["mirror_match"]["nik_masked"] == "3273********0099"
    assert receiver.metadata["mirror_match"]["simulation"] is True


def test_bridge_fails_closed_if_mirror_mapping_disappears(mirror):
    scenario = get_scenario("direct-va-loopback")
    handoff = FNAMoneyTrailHandoffInput(**scenario["handoff"])
    nodes, edges = FNAHandoffAdapter().import_snapshot(handoff, scenario)
    bridge = P2PAndPAKDBridge(database=mirror)
    bridge.resolve_onramp(handoff, scenario, nodes, edges)
    ScenarioTrailer().trace_fixture(scenario, nodes, edges)
    mirror.connection.execute("DELETE FROM mirror_pakd_wallets WHERE wallet_type='CUSTODIAL_DEPOSIT_ADDRESS'")
    with pytest.raises(ValueError, match="custodial"):
        bridge.resolve_offramp(scenario, nodes, edges)


def test_forward_join_does_not_attribute_another_users_withdrawal(mirror):
    mirror.connection.execute("UPDATE mirror_pakd_withdrawals SET pakd_name='Indodax',user_uid='IDX-DEMO-099' WHERE deposit_id='PAKD-IN-0002'")
    result = mirror.lookup_deposit_by_fna_handoff("880800007500", "RRN-DEMO-0002", "120000000")
    assert result["user_uid"] == "TKC-DEMO-077"
    assert result["initial_wallet_address"] is None
    assert result["withdrawal_tx_hash"] is None


@pytest.mark.parametrize("pakd_name,user_uid", [(None,"PTU-DEMO-010"),("Pintu",None),("", "PTU-DEMO-010"),("Pintu", {})])
def test_dynamic_wallet_registration_rejects_invalid_profile_keys(mirror, pakd_name, user_uid):
    with pytest.raises(ValueError):
        mirror.add_wallet("0x" + "b" * 40, "ETHEREUM_ERC20", pakd_name, user_uid)
