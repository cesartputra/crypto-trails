"""SQLite replica for the demo. All identities and balances are synthetic seeds."""
from decimal import Decimal, InvalidOperation
import re
import sqlite3
from threading import RLock
from scenarios import SCENARIOS, RATE

D = Decimal
SOURCE = "SYNTHETIC_PAKD_MIRROR_DB_SQLITE"
TABLES = ("mirror_pakd_users", "mirror_pakd_fiat_deposits", "mirror_pakd_wallets", "mirror_pakd_withdrawals")


def _amount(value):
    try:
        amount = D(str(value))
        if not amount.is_finite() or amount < 0 or amount > D("1000000000000") or amount != amount.quantize(D(".01")):
            raise ValueError()
        return format(amount.quantize(D(".01")), "f")
    except (InvalidOperation, ValueError):
        raise ValueError("Nominal Mirror DB harus Rupiah non-negatif dengan maksimal dua desimal") from None


def _address(value, chain):
    if chain in {"ETHEREUM_ERC20", "BSC_BEP20"}:
        if not isinstance(value, str) or not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
            raise ValueError("Alamat EVM Mirror DB tidak valid")
        return value.lower()
    if chain == "TRON_TRC20" and isinstance(value, str) and re.fullmatch(r"T[A-Za-z0-9]{20,99}", value):
        return value
    raise ValueError("Jaringan/alamat Mirror DB tidak didukung")


class PakdMirrorDB:
    def __init__(self, database=":memory:", seed=True):
        self._lock = RLock()
        self.connection = sqlite3.connect(database, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript("""
        CREATE TABLE IF NOT EXISTS mirror_pakd_users (
            pakd_name TEXT NOT NULL, user_uid TEXT NOT NULL, nik_masked TEXT NOT NULL,
            full_name TEXT NOT NULL, registered_phone TEXT NOT NULL, kyc_status TEXT NOT NULL,
            remaining_pakd_balance_idr TEXT NOT NULL DEFAULT '0.00',
            PRIMARY KEY (pakd_name, user_uid));
        CREATE TABLE IF NOT EXISTS mirror_pakd_fiat_deposits (
            deposit_id TEXT PRIMARY KEY, pakd_name TEXT NOT NULL, user_uid TEXT NOT NULL,
            va_or_account_number TEXT NOT NULL, rrn_reference TEXT NOT NULL, amount_idr TEXT NOT NULL,
            deposit_timestamp TEXT NOT NULL, intake_type TEXT NOT NULL,
            FOREIGN KEY (pakd_name,user_uid) REFERENCES mirror_pakd_users(pakd_name,user_uid));
        CREATE TABLE IF NOT EXISTS mirror_pakd_wallets (
            wallet_address TEXT NOT NULL, chain TEXT NOT NULL, pakd_name TEXT NOT NULL,
            user_uid TEXT NOT NULL, wallet_type TEXT NOT NULL CHECK(wallet_type IN
                ('CUSTODIAL_DEPOSIT_ADDRESS','REGISTERED_WITHDRAWAL_WHITELIST')),
            PRIMARY KEY (wallet_address,chain),
            FOREIGN KEY (pakd_name,user_uid) REFERENCES mirror_pakd_users(pakd_name,user_uid));
        CREATE TABLE IF NOT EXISTS mirror_pakd_withdrawals (
            withdrawal_id TEXT PRIMARY KEY, pakd_name TEXT NOT NULL, user_uid TEXT NOT NULL,
            token_symbol TEXT NOT NULL, crypto_amount TEXT NOT NULL, chain TEXT NOT NULL,
            tx_hash TEXT NOT NULL, initial_wallet_address TEXT NOT NULL, withdrawal_timestamp TEXT NOT NULL,
            deposit_id TEXT REFERENCES mirror_pakd_fiat_deposits(deposit_id),
            FOREIGN KEY (pakd_name,user_uid) REFERENCES mirror_pakd_users(pakd_name,user_uid));
        CREATE INDEX IF NOT EXISTS mirror_deposit_lookup ON mirror_pakd_fiat_deposits
            (va_or_account_number,rrn_reference,amount_idr);
        """)
        if seed:
            self._seed()

    def _seed(self):
        with self._lock, self.connection:
            for index, scenario in enumerate(SCENARIOS.values(), 1):
                ramp = scenario["onramp"]
                self.connection.execute("INSERT OR IGNORE INTO mirror_pakd_users VALUES (?,?,?,?,?,?,?)", (
                    ramp["platform"], ramp["uid"], ramp["kyc"]["nik_masked"], ramp["kyc"]["name"],
                    f"+62-812-****-{index:04d}", ramp["kyc"]["status"], _amount(ramp.get("held_escrow_idr", "0"))))
                initial = next(w for w in scenario["wallets"] if w["id"] == ramp["initial_wallet"])
                for intake in scenario["pakd_intake_ledger"]:
                    self.connection.execute("INSERT OR IGNORE INTO mirror_pakd_fiat_deposits VALUES (?,?,?,?,?,?,?,?)", (
                        intake["intake_id"], ramp["platform"], ramp["uid"], intake["gateway_account"],
                        intake["rrn_reference_number"], _amount(intake["fiat_amount_idr"]), intake["timestamp"], intake["intake_mechanism"]))
                    self.connection.execute("INSERT OR IGNORE INTO mirror_pakd_withdrawals VALUES (?,?,?,?,?,?,?,?,?,?)", (
                        f"WD-{index:04d}-{intake['intake_id']}", ramp["platform"], ramp["uid"], ramp["asset"], ramp["crypto_amount"],
                        ramp["chain"], ramp["withdrawal_tx_hash"], _address(initial["address"], ramp["chain"]),
                        ramp["withdrawal_timestamp"], intake["intake_id"]))
                self.add_wallet(initial["address"], ramp["chain"], ramp["platform"], ramp["uid"], "REGISTERED_WITHDRAWAL_WHITELIST")
                for wallet in scenario["wallets"]:
                    if wallet.get("recovery_institution") != "PAKD":
                        continue
                    platform, uid = wallet["platform"], wallet["uid"]
                    self.connection.execute("INSERT OR IGNORE INTO mirror_pakd_users VALUES (?,?,?,?,?,?,?)", (
                        platform, uid, wallet.get("kyc_nik_masked", ramp["kyc"]["nik_masked"]),
                        f"Nasabah Penerima Demo {uid}" if uid != ramp["uid"] else ramp["kyc"]["name"],
                        f"+62-813-****-{index:04d}", "SYNTHETIC_VERIFIED", _amount(D(wallet["balance_crypto"]) * RATE)))
                    self.connection.execute("UPDATE mirror_pakd_users SET remaining_pakd_balance_idr=? WHERE pakd_name=? AND user_uid=?", (
                        _amount(D(wallet["balance_crypto"]) * RATE), platform, uid))
                    self.add_wallet(wallet["address"], ramp["chain"], platform, uid, "CUSTODIAL_DEPOSIT_ADDRESS")
            # Pintu/Reku examples make the schema preview representative without labeling public addresses as customers.
            for name, uid, nik in (("Pintu", "PTU-DEMO-010", "3173********0010"), ("Reku", "RKU-DEMO-011", "3273********0011")):
                self.connection.execute("INSERT OR IGNORE INTO mirror_pakd_users VALUES (?,?,?,?,?,?,?)", (
                    name, uid, nik, f"Nasabah Demo {name}", "+62-811-****-0000", "SYNTHETIC_VERIFIED", "0.00"))

    def lookup_deposit_by_fna_handoff(self, va_number, rrn_reference, amount_idr):
        """A unique parameterized VA/RRN/amount join; no guess from approximate amounts."""
        amount = _amount(amount_idr)
        with self._lock:
            rows = self.connection.execute("""
                SELECT u.*,d.deposit_id,d.va_or_account_number,d.rrn_reference,d.amount_idr,
                       d.deposit_timestamp,d.intake_type,w.withdrawal_id,w.token_symbol,w.crypto_amount,
                       w.chain,w.tx_hash AS withdrawal_tx_hash,w.initial_wallet_address,w.withdrawal_timestamp
                FROM mirror_pakd_fiat_deposits d
                JOIN mirror_pakd_users u ON u.pakd_name=d.pakd_name AND u.user_uid=d.user_uid
                LEFT JOIN mirror_pakd_withdrawals w ON w.deposit_id=d.deposit_id
                  AND w.pakd_name=d.pakd_name AND w.user_uid=d.user_uid
                WHERE d.va_or_account_number=? AND d.rrn_reference=? AND d.amount_idr=? LIMIT 3
                """, (va_number, rrn_reference, amount)).fetchall()
        if len(rows) > 1:
            raise ValueError("Pencocokan deposit/withdrawal Mirror DB ambigu; review manual diperlukan")
        return self._record(rows[0]) if rows else None

    def match_wallet_in_mirror_db(self, wallet_address, chain="ETHEREUM_ERC20"):
        address = _address(wallet_address, chain)
        with self._lock:
            row = self.connection.execute("""
                SELECT w.*,u.nik_masked,u.full_name,u.registered_phone,u.kyc_status,u.remaining_pakd_balance_idr
                FROM mirror_pakd_wallets w JOIN mirror_pakd_users u
                  ON u.pakd_name=w.pakd_name AND u.user_uid=w.user_uid
                WHERE w.wallet_address=? AND w.chain=?
                """, (address, chain)).fetchone()
        return self._record(row) if row else None

    @staticmethod
    def _record(row):
        return {**dict(row), "evidence_source": SOURCE, "simulation": True,
                "identity_note": "Identitas sintetis pada replika demo; bukan data nasabah PAKD nyata."}

    def add_wallet(self, wallet_address, chain, pakd_name, user_uid, wallet_type="REGISTERED_WITHDRAWAL_WHITELIST"):
        address = _address(wallet_address, chain)
        if not all(isinstance(value, str) and value.strip() and len(value) <= 120 for value in (pakd_name, user_uid)):
            raise ValueError("Nama PAKD dan UID harus berupa teks non-kosong maksimal 120 karakter")
        if wallet_type not in {"CUSTODIAL_DEPOSIT_ADDRESS", "REGISTERED_WITHDRAWAL_WHITELIST"}:
            raise ValueError("Jenis wallet Mirror DB tidak didukung")
        with self._lock, self.connection:
            existing = self.connection.execute("SELECT pakd_name,user_uid,wallet_type FROM mirror_pakd_wallets WHERE wallet_address=? AND chain=?", (address, chain)).fetchone()
            if existing and tuple(existing) != (pakd_name, user_uid, wallet_type):
                raise ValueError("Alamat Mirror DB sudah dipetakan ke profil/tipe lain")
            try:
                self.connection.execute("INSERT OR IGNORE INTO mirror_pakd_wallets VALUES (?,?,?,?,?)", (address, chain, pakd_name, user_uid, wallet_type))
            except sqlite3.IntegrityError:
                raise ValueError("Profil user PAKD harus tersedia sebelum wallet didaftarkan") from None
        return self.match_wallet_in_mirror_db(address, chain)

    def get_mirror_db_preview(self):
        with self._lock:
            # Table names are a fixed allowlist, never request data.
            tables = {name: [dict(row) for row in self.connection.execute(f"SELECT * FROM {name} LIMIT 100")] for name in TABLES}
            counts = {name: self.connection.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0] for name in TABLES}
        return {"database": "SQLite PAKD Mirror DB", "simulation": True, "evidence_source": SOURCE,
                "tables": tables, "row_counts": counts, "preview_limit": 100,
                "warning": "Seluruh rekaman adalah seed sintetis demo; bukan replika operasional OJK/PAKD."}


mirror_db = PakdMirrorDB()
