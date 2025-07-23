from bip_utils import Bip32Slip10Ed25519, Bip39SeedGenerator, Bip39MnemonicValidator
from stellar_sdk import Keypair, StrKey, Server, TransactionBuilder, Asset
from datetime import datetime, timezone, timedelta
import time, threading

TEN_SECONDS = 2.00  # Waktu siaga sebelum unlock (2 detik)
UNLOCK_ID = None
UNLOCK_TIME = None
UNLOCK_BALANCE = None
REMAIN_BALANCE = 1
UNLOCKS = False
NETWORK_PASSPHRASE = "Pi Network"
HORIZON_URL = "https://api.mainnet.minepi.com"
BASE_FEE = 20000000  # 4 Pi total per transaksi

# Zona waktu lokal Anda
your_timezone = timezone(timedelta(hours=1))
now_utc = datetime.now(timezone.utc)
today_utc_str = now_utc.strftime("%Y-%m-%d")

# Fee payer (Sponsor)
TX_PAYER = ""
KP_TX_PAYER = Keypair.from_secret(TX_PAYER)
TX_PAYER_AD = KP_TX_PAYER.public_key
DESTINATION_ADDRESS = ""

mnemonic = input("Enter Passphrase: ")

if not Bip39MnemonicValidator().IsValid(mnemonic):
    raise ValueError("Invalid mnemonic")

seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed_bytes)
derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
priv_key_bytes = derived.PrivateKey().Raw().ToBytes()
SECRET_KEY = StrKey.encode_ed25519_secret_seed(priv_key_bytes)
PUBLIC_KEY = Keypair.from_secret(SECRET_KEY)
ACCOUNT_ID = PUBLIC_KEY.public_key

server = Server(HORIZON_URL)
KP = Keypair.from_secret(SECRET_KEY)
ACCOUNT_ID = KP.public_key
claimables = server.claimable_balances().for_claimant(ACCOUNT_ID).limit(5).call()
total_claimable = sum(float(c["amount"]) for c in claimables["_embedded"]["records"])
print(f"[CLAIMABLE] Found {len(claimables['_embedded']['records'])} claimable balances.")

def checking_claimable_balances():
    for c in claimables["_embedded"]["records"]:
        balance_id = c["id"]
        for claimant in c["claimants"]:
            if claimant["destination"] != ACCOUNT_ID:
                continue
            predicate = claimant["predicate"]
            if "not" in predicate and "abs_before" in predicate["not"]:
                unlock_utc = datetime.fromisoformat(predicate["not"]["abs_before"].replace("Z", "+00:00"))
                unlock_local = unlock_utc.astimezone(your_timezone)
                time_diff = (unlock_utc - now_utc).total_seconds()
                print(f"\nBalance ID: {balance_id} - {c['amount']}")
                print(f" - Unlocks At (UTC):   {unlock_utc.strftime('%Y-%m-%d %H:%M:%S')}")
                print(f" - Unlocks At (+1hr):  {unlock_local.strftime('%Y-%m-%d %H:%M:%S')}")
                if unlock_utc.strftime("%Y-%m-%d") == today_utc_str:
                    if 0 < time_diff <= TEN_SECONDS:
                        print(f"Unlocks in {time_diff:.2f}s (today, soon)")
                    elif time_diff <= 0:
                        print("Already unlocked today.")
                    else:
                        print(f" ⏳ Unlocks later today in {time_diff:.2f}s")
                elif time_diff <= 0:
                    print("Already unlocked (earlier day)")
                else:
                    print(f" ⏳ Still locked for {time_diff:.2f}s")
            elif predicate == {"unconditional": True}:
                print(f"\nBalance ID: {balance_id}")
                print("Unconditional claim – can claim now.")

def nextunlock():
    global UNLOCK_TIME, UNLOCK_ID, UNLOCK_BALANCE, UNLOCKS
    for c in claimables["_embedded"]["records"]:
        for claimant in c["claimants"]:
            if claimant["destination"] != ACCOUNT_ID:
                continue
            predicate = claimant["predicate"]
            if "not" in predicate and "abs_before" in predicate["not"]:
                unlock_utc = datetime.fromisoformat(predicate["not"]["abs_before"].replace("Z", "+00:00"))
                unlock_local = unlock_utc.astimezone(your_timezone)
                if UNLOCK_TIME is None or unlock_local < UNLOCK_TIME:
                    UNLOCK_TIME = unlock_local
                    UNLOCK_ID = c["id"]
                    amt = format(round(float(c["amount"]) - REMAIN_BALANCE, 6), '.6f')
                    UNLOCK_BALANCE = str(amt)
                    UNLOCKS = True

def transaction_builder_(local_acc):
    tx = (
        TransactionBuilder(source_account=local_acc, network_passphrase=NETWORK_PASSPHRASE, base_fee=BASE_FEE)
        .append_claim_claimable_balance_op(balance_id=UNLOCK_ID, source=ACCOUNT_ID)
        .append_payment_op(destination=DESTINATION_ADDRESS, amount=UNLOCK_BALANCE, asset=Asset.native(), source=ACCOUNT_ID)
        .add_text_memo("OK")
        .set_timeout(30)
        .build()
    )
    tx.sign(KP)
    tx.sign(KP_TX_PAYER)
    return tx

def reload():
    return server.load_account(TX_PAYER_AD)

SEQUENCE = int(reload().sequence)
ACCOUNT = reload()

def countdown():
    global ACCOUNT, SEQUENCE
    tx_prepared = None
    while True:
        now = datetime.now(timezone.utc)
        if UNLOCK_TIME:
            time_diff = (UNLOCK_TIME - now).total_seconds()
            if 1.8 <= time_diff <= 2.2:
                print("[PREPARE] Building transaction ~2s before unlock...")
                ACCOUNT = reload()
                SEQUENCE = ACCOUNT.sequence
                tx_prepared = transaction_builder_(local_acc=ACCOUNT)
            elif time_diff <= 0:
                print("[SEND] Unlock time reached. Submitting transaction...")
                if tx_prepared:
                    try:
                        response = server.submit_transaction(tx_prepared)
                        print("[PASS] Transaction Submitted")
                        print(response)
                    except Exception as e:
                        print(f"[ERROR] Submit failed: {e}")
                break
            elif time_diff <= 5:
                print(f"[WAITING] {time_diff:.2f}s left until unlock.")
            else:
                print(f"[WAIT] {time_diff:.2f}s left.")
        else:
            print("No unlock time set.")
            break
        time.sleep(0.01)

print("[CLAIMABLE] Checking claimable balances...")
checking_claimable_balances()

print("[CLAIMABLE] Checking for next unlock time...")
nextunlock()

if UNLOCK_TIME:
    print(f"[CLAIMABLE] Next unlock time: {UNLOCK_TIME} for Balance ID: {UNLOCK_ID} - {UNLOCK_BALANCE} Pi")
    print("[CLAIMABLE] Starting countdown...")
    countdown()
else:
    print("No claimable balances found.")

print(f"[CLAIMABLE] Total: {total_claimable:.6f} Pi")
