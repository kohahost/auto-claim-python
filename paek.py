from bip_utils import Bip32Slip10Ed25519, Bip39SeedGenerator, Bip39MnemonicValidator
from stellar_sdk import Keypair, StrKey, Server, TransactionBuilder, Asset
from datetime import datetime, timezone, timedelta
import time, threading

TEN_SECONDS = 10.00
UNLOCK_ID = None
UNLOCK_TIME = None
UNLOCK_BALANCE = None
REMAIN_BALANCE = 1
UNLOCKS = False
NETWORK_PASSPHRASE = "Pi Network"
HORIZON_URL = "https://api.mainnet.minepi.com"
BASE_FEE = 100000
your_timezone = timezone(timedelta(hours=1))
now_utc = datetime.now(timezone.utc)
today_utc_str = now_utc.strftime("%Y-%m-%d")
STOP_TIME = 10

# Transaction fee Payer

# "GDNP5YR62K653C4JSR7RLVANDES5YC2FBXP64TCJA4APSZQPKKMJWJMC"
TX_PAYER = ""
KP_TX_PAYER = Keypair.from_secret(TX_PAYER)
TX_PAYER_AD = KP_TX_PAYER.public_key
DESTINATION_ADDRESS = ""

mnemonic = input("Enter Passphrase: ")

# Validate mnemonic
if not Bip39MnemonicValidator().IsValid(mnemonic):
    raise ValueError("Invalid mnemonic")

# Generate seed and create master key
seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed_bytes)

# Derive key at m/44'/314159'/0'
derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
priv_key_bytes = derived.PrivateKey().Raw().ToBytes()

# Get Stellar-compatible keys
SECRET_KEY = StrKey.encode_ed25519_secret_seed(priv_key_bytes)
PUBLIC_KEY = Keypair.from_secret(SECRET_KEY)
ACCOUNT_ID = PUBLIC_KEY.public_key

# Setup server connection
server = Server(HORIZON_URL)

KP = Keypair.from_secret(SECRET_KEY)
ACCOUNT_ID = KP.public_key
claimables = server.claimable_balances().for_claimant(ACCOUNT_ID).limit(5).call()
total_claimable = sum(float(c["amount"]) for c in claimables["_embedded"]["records"])
print(f"[CLAIMABLE] Found {len(claimables['_embedded']['records'])} claimable balances.")


def checking_claimable_balances():
    for c in claimables["_embedded"]["records"]:
        balance_id = c["id"]
        claimants = c["claimants"]

        for claimant in claimants:
            if claimant["destination"] != ACCOUNT_ID:
                continue

            predicate = claimant["predicate"]
            can_claim_now = False

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
                        can_claim_now = True
                    else:
                        print(f" ⏳ Unlocks later today in {time_diff:.2f}s")
                elif time_diff <= 0:
                    print("Already unlocked (earlier day)")
                    can_claim_now = True
                else:
                    print(f" ⏳ Still locked for {time_diff:.2f}s")

            elif predicate == {"unconditional": True}:
                print(f"\nBalance ID: {balance_id}")
                print("Unconditional claim – can claim now.")
                can_claim_now = True

            if can_claim_now:
                print(f"Ready to claim: {balance_id}")


def nextunlock():
    global UNLOCK_TIME, UNLOCK_ID, UNLOCK_BALANCE, REMAIN_BALANCE, UNLOCKS
    if claimables:
        for c in claimables["_embedded"]["records"]:
            if len(claimables['_embedded']['records']) >= 1:
                balance_ID = c["id"]
                claimants = c["claimants"]
                for claimant in claimants:
                    if claimant["destination"] != ACCOUNT_ID:
                        continue

                    predicate = claimant["predicate"] 
                    if "not" in predicate and "abs_before" in predicate["not"]:
                        unlock_utc = datetime.fromisoformat(predicate["not"]["abs_before"].replace("Z", "+00:00"))
                        unlock_local = unlock_utc.astimezone(your_timezone)

                        if UNLOCK_TIME is None or unlock_local < UNLOCK_TIME:
                            UNLOCK_TIME = unlock_local
                            UNLOCK_ID = balance_ID
                            balance_amount = c["amount"]
                            amt = format(round(float(balance_amount) - REMAIN_BALANCE, 6), '.6f')
                            print(f"This is the amount: {amt}")
                            UNLOCK_BALANCE = str(amt)
                            UNLOCKS = True
                        
            else:
                unlock_utc = datetime.fromisoformat(predicate["not"]["abs_before"].replace("Z", "+00:00"))
                unlock_local = unlock_utc.astimezone(your_timezone)
                print("Only 1 claimable balances found.")
                balance_ID = c["id"]
                UNLOCK_ID = balance_ID
                UNLOCK_TIME = unlock_local
                balance_amount = c["amount"]
                amt = format(round(float(balance_amount) - REMAIN_BALANCE, 6), '.6f')
                UNLOCK_BALANCE = str(amt)
                UNLOCKS = True
    else:
        print("No claimable balances found.")
        UNLOCK_TIME = None
        UNLOCK_ID = None
        UNLOCK_BALANCE = None
        UNLOCKS = False


def countdown():
    global UNLOCK_TIME
    while True:
        now = datetime.now(timezone.utc)
        if UNLOCK_TIME is not None:
            time_diff = (UNLOCK_TIME - now).total_seconds()
            if time_diff <= 0:
                print("Unlock time reached!")
                break
            elif time_diff <= TEN_SECONDS:
                print(f"Unlocking soon! {time_diff:.2f}s left.")
                break
            else:
                print(f"[WAIT] Time left until unlock: {time_diff:.2f}s")
                pass
        else:
            print("No unlock time set.")
            break

        time.sleep(0.01)


def transaction_builder_(local_acc):
    global NETWORK_PASSPHRASE, BASE_FEE, UNLOCK_ID, ACCOUNT_ID, DESTINATION_ADDRESS, UNLOCK_BALANCE
    tx = (
        TransactionBuilder(source_account=local_acc, network_passphrase=NETWORK_PASSPHRASE, base_fee=BASE_FEE)
        .append_claim_claimable_balance_op(balance_id=UNLOCK_ID, source=ACCOUNT_ID)
        .append_payment_op(destination=DESTINATION_ADDRESS, amount=UNLOCK_BALANCE, asset=Asset.native(), source=ACCOUNT_ID)
        .add_text_memo(f"OK")
        .set_timeout(30)
        .build()
    )
    tx.sign(KP)
    tx.sign(KP_TX_PAYER)

    return tx


SEQUENCE = None
def account_sequence():
    account = server.load_account(TX_PAYER_AD)
    return account.sequence


SEQUENCE = int(account_sequence())
sequence_lock = threading.Lock()

ACCOUNT = None
def reload():
    account = server.load_account(TX_PAYER_AD)
    return account
tx_account = reload()

ACCOUNT = tx_account

def claim_and_send(i, seq):
    global SEQUENCE, ACCOUNT
    
    LOCAL_ACCOUNT = ACCOUNT
    LOCAL_ACCOUNT.sequence = seq

    transaction_b = transaction_builder_(local_acc=LOCAL_ACCOUNT)

    try:
        response = server.submit_transaction(transaction_b)
        print(f"[PASS] Transaction -{i}- Submitted")
    except Exception as e:
        print(f"[ERROR] Transaction -{i}- Error")


def start_spamming():
    global SEQUENCE, ACCOUNT, UNLOCKS, server, BASE_FEE
    i = 0
    while True:
        if datetime.now(your_timezone) >= UNLOCK_TIME + timedelta(seconds=STOP_TIME):
            print(f"Stopping spamming - {STOP_TIME} seconds passed since unlock.")
            break

        for _ in range(1):
            seq = SEQUENCE
            x = threading.Thread(target=claim_and_send, args=(i,seq)).start()
            i += 1
            SEQUENCE += 1

        if i % 20 == 0:
            threading.Event().wait(0.0001)
            ACCOUNT = reload()
            SEQUENCE = ACCOUNT.sequence
        else:
            pass



# cheacking claimable balances
print(f"[CLAIMABLE] Checking claimable balances...")
checking_claimable_balances()

print(f"[CLAIMABLE] Checking for next unlock time...")
nextunlock()

# Print the next unlock time and balance ID
if UNLOCK_TIME is not None:
    print(f"[CLAIMABLE] Next unlock time: {UNLOCK_TIME} for Balance ID: {UNLOCK_ID} - {UNLOCK_BALANCE} Pi")
else:
    print("No claimable balances found.")

# Start countdown
print(f"[CLAIMABLE] Starting countdown...")
countdown()

if UNLOCKS == True:
    ACCOUNT = reload()
    SEQUENCE = ACCOUNT.sequence
    start_spamming()
else:
    print("No claimable balances found.")
    print("Exiting...")

print(f"[CLAIMABLE] Total: {total_claimable:.6f} Pi")