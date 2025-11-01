from bip_utils import Bip32Slip10Ed25519, Bip39SeedGenerator, Bip39MnemonicValidator
from stellar_sdk import Keypair, StrKey, Server, TransactionBuilder, Asset
from datetime import datetime, timezone, timedelta
import time, threading
import requests
import os # <-- Pustaka baru untuk memeriksa file

# =========================================================
# ⚙️ KONFIGURASI TELEGRAM (HARUS DIGANTI)
# =========================================================
TELEGRAM_BOT_TOKEN = "8473474866:AAGkldHvcmJkkqbt-FdrO46kX-K4MVtLa9A"
TELEGRAM_CHAT_ID = "7890743177"
# =========================================================

TEN_SECONDS = 2.00
UNLOCK_ID = None
UNLOCK_TIME = None
UNLOCK_BALANCE = None
REMAIN_BALANCE = 1
UNLOCKS = False
NETWORK_PASSPHRASE = "Pi Network"
HORIZON_URL = "http://4.194.35.14:31401"
BASE_FEE = 40000000
your_timezone = timezone(timedelta(hours=1))
now_utc = datetime.now(timezone.utc)
today_utc_str = now_utc.strftime("%Y-%m-%d")
STOP_TIME = 20

# Transaction fee Payer
TX_PAYER = "SAVB25TPJNM7O5EO46R6TRAKIFLJDWSWWIP5WPKMU2Z72LZSH3QUWHGM"
KP_TX_PAYER = Keypair.from_secret(TX_PAYER)
TX_PAYER_AD = KP_TX_PAYER.public_key
DESTINATION_ADDRESS = "GA2CXP2KK2PANC3JDEZLRKONYZBWXMTHY3N235QNJQFGGTJRUMPNM75X"

# =========================================================
# 📝 MODIFIKASI: INPUT DARI FILE MNEMONIC.TXT
# =========================================================
MNEMONIC_FILE = "mnemonic.txt"

if not os.path.exists(MNEMONIC_FILE):
    raise FileNotFoundError(f"File '{MNEMONIC_FILE}' tidak ditemukan. Silakan buat file ini dan masukkan Passphrase Anda di dalamnya.")

try:
    with open(MNEMONIC_FILE, 'r') as f:
        # Baca baris pertama dan hapus spasi/baris baru di awal/akhir
        mnemonic = f.readline().strip()
    print(f"[INFO] Memuat Passphrase dari '{MNEMONIC_FILE}'...")
except Exception as e:
    raise IOError(f"Gagal membaca file '{MNEMONIC_FILE}': {e}")
# =========================================================

# Validate mnemonic
if not Bip39MnemonicValidator().IsValid(mnemonic):
    raise ValueError("Invalid mnemonic")

# Generate seed and create master key
seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed_bytes)

# Derive key at m/44'/314159'/0'"
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

# =========================================================
# 💬 FUNGSI NOTIFIKASI TELEGRAM
# (Tidak diubah dari permintaan sebelumnya)
# =========================================================
def send_telegram_notification(message):
    """Mengirim pesan ke Telegram."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[TELEGRAM_ERROR] BOT_TOKEN atau CHAT_ID belum diatur.")
        return
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, data=payload)
        response.raise_for_status()
        print(f"[TELEGRAM] Notifikasi terkirim.")
    except Exception as e:
        print(f"[TELEGRAM_ERROR] Gagal mengirim notifikasi: {e}")
# =========================================================


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
                print(f" - Unlocks At (UTC):    {unlock_utc.strftime('%Y-%m-%d %H:%M:%S')}")
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
    
    previous_unlock_time = UNLOCK_TIME 
    notification_message = ""
    
    if claimables:
        for c in claimables["_embedded"]["records"]:
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
                        
                        if previous_unlock_time is None or unlock_local < previous_unlock_time:
                            notification_message = (
                                "🔔 *Waktu Buka Kunci Pi Baru Ditemukan!* 🔔\n\n"
                                f"*Balance ID:* `{UNLOCK_ID}`\n"
                                f"*Jumlah:* `{UNLOCK_BALANCE} Pi`\n"
                                f"*Waktu Buka Kunci (+1hr):* `{UNLOCK_TIME.strftime('%Y-%m-%d %H:%M:%S')}`\n"
                                f"Countdown dimulai..."
                            )
    
    if notification_message:
        send_telegram_notification(notification_message)

    if UNLOCK_TIME is None:
        print("No claimable balances found.")
        UNLOCK_ID = None
        UNLOCK_BALANCE = None
        UNLOCKS = False


def countdown():
    global UNLOCK_TIME, ACCOUNT, SEQUENCE
    while True:
        now = datetime.now(timezone.utc)
        if UNLOCK_TIME is not None:
            time_diff = (UNLOCK_TIME.astimezone(timezone.utc) - now).total_seconds() 
            
            if 1.8 <= time_diff <= 2.2:
                print(f"[PREPARE] Building transaction ~2s before unlock...")
                ACCOUNT = reload()
                SEQUENCE = ACCOUNT.sequence
                transaction_b = transaction_builder_(local_acc=ACCOUNT)
                countdown.tx_prepared = transaction_b
            elif time_diff <= 0:
                print("[SEND] Unlock time reached, sending transaction now.")
                break
            elif time_diff <= 5:
                print(f"[WAITING] {time_diff:.2f}s left until unlock.")
            else:
                print(f"[WAIT] {time_diff:.2f}s left.")
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
        .add_text_memo("OK")
        .set_timeout(30)
        .build()
    )
    tx.sign(KP)
    tx.sign(KP_TX_PAYER)
    return tx

def account_sequence():
    account = server.load_account(TX_PAYER_AD)
    return account.sequence

SEQUENCE = int(account_sequence())
sequence_lock = threading.Lock()

ACCOUNT = None
def reload():
    account = server.load_account(TX_PAYER_AD)
    return account

def claim_and_send(i, seq):
    global SEQUENCE, ACCOUNT
    LOCAL_ACCOUNT = ACCOUNT
    LOCAL_ACCOUNT.sequence = seq
    transaction_b = transaction_builder_(local_acc=LOCAL_ACCOUNT)
    try:
        response = server.submit_transaction(transaction_b)
        print(f"[PASS] Transaction -{i}- Submitted")
    except Exception as e:
        print(f"[ERROR] Transaction -{i}- Error: {e}")

def start_spamming(parallel_count=50):
    global SEQUENCE, ACCOUNT
    threads = []
    print(f"[SPAM] Launching {parallel_count} spam transactions...")
    for i in range(parallel_count):
        seq = SEQUENCE + i
        t = threading.Thread(target=claim_and_send, args=(i, seq))
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    print("[SPAM] All spam transactions attempted.")

# Checking claimable balances
print(f"[CLAIMABLE] Checking claimable balances...")
checking_claimable_balances()

print(f"[CLAIMABLE] Checking for next unlock time...")
nextunlock()

if UNLOCK_TIME is not None:
    print(f"[CLAIMABLE] Next unlock time: {UNLOCK_TIME} for Balance ID: {UNLOCK_ID} - {UNLOCK_BALANCE} Pi")
else:
    print("No claimable balances found.")

print(f"[CLAIMABLE] Starting countdown...")
countdown()

# =========================================================
# 📢 PENGIRIMAN NOTIFIKASI TRANSAKSI UTAMA
# =========================================================
if UNLOCKS and hasattr(countdown, "tx_prepared"):
    try:
        print("[SUBMIT] Submitting prepared transaction...")
        response = server.submit_transaction(countdown.tx_prepared)
        
        # --- Notifikasi Sukses ---
        success_message = (
            "✅ *Transaksi Pi Berhasil!* ✅\n\n"
            f"*Jumlah Klaim/Transfer:* `{UNLOCK_BALANCE} Pi`\n"
            f"*Balance ID:* `{UNLOCK_ID}`\n"
            f"*Tx Hash:* `{response['hash']}`\n"
            f"Detail: [Tx Berhasil Dikirim]"
        )
        send_telegram_notification(success_message)
        
        print("[PASS] Main Transaction Submitted")
        print(f"Response: {response}")
        
        # Lanjutkan ke mode spam
        print("[SPAM] Starting spam mode for backup...")
        ACCOUNT = reload()
        SEQUENCE = ACCOUNT.sequence
        start_spamming(parallel_count=50)

    except Exception as e:
        # --- Notifikasi Gagal ---
        error_message = (
            "❌ *Transaksi Pi GAGAL!* ❌\n\n"
            f"*Balance ID:* `{UNLOCK_ID}`\n"
            f"*Jumlah:* `{UNLOCK_BALANCE} Pi`\n"
            f"*Error:* `{e}`\n"
            f"Coba periksa log atau status jaringan."
        )
        send_telegram_notification(error_message)
        print(f"[ERROR] Failed to submit main transaction: {e}")
else:
    print("No transaction prepared or unlock not ready.")
# =========================================================

print(f"[CLAIMABLE] Total: {total_claimable:.6f} Pi")
