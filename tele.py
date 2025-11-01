from bip_utils import Bip32Slip10Ed25519, Bip39SeedGenerator, Bip39MnemonicValidator
from stellar_sdk import Keypair, StrKey, Server, TransactionBuilder, Asset
from datetime import datetime, timezone, timedelta
import time, threading, os, json, requests

# ==============================
# Konfigurasi dasar
# ==============================
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

# Transaction fee payer
TX_PAYER = "SAVB25TPJNM7O5EO46R6TRAKIFLJDWSWWIP5S"
KP_TX_PAYER = Keypair.from_secret(TX_PAYER)
TX_PAYER_AD = KP_TX_PAYER.public_key
DESTINATION_ADDRESS = "GA2CXP2KK2PANC3JDEZLRKONYZBWXMTHY3N235QNJQFGGTJRUMPNM75X"

# ==============================
# Baca mnemonic dari file
# ==============================
def read_mnemonic_from_file(path='mnemonics.txt'):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if (line.startswith('"') and line.endswith('"')) or (line.startswith("'") and line.endswith("'")):
                    line = line[1:-1]
                return line
    except FileNotFoundError:
        raise FileNotFoundError(f"File '{path}' tidak ditemukan. Buat file dengan mnemonic pada baris pertama.")
    return None

mnemonic = read_mnemonic_from_file('mnemonics.txt')
if not mnemonic:
    raise ValueError("Mnemonic tidak ditemukan di file. Pastikan ada baris non-kosong di 'mnemonics.txt'.")

if not Bip39MnemonicValidator().IsValid(mnemonic):
    raise ValueError("Mnemonic tidak valid.")

# ==============================
# Buat keypair dari mnemonic
# ==============================
seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed_bytes)
derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
priv_key_bytes = derived.PrivateKey().Raw().ToBytes()

SECRET_KEY = StrKey.encode_ed25519_secret_seed(priv_key_bytes)
PUBLIC_KEY = Keypair.from_secret(SECRET_KEY)
ACCOUNT_ID = PUBLIC_KEY.public_key

KP = Keypair.from_secret(SECRET_KEY)
server = Server(HORIZON_URL)

# ==============================
# Telegram Config & Functions
# ==============================
def load_telegram_config(cfg_path='telegram.cfg'):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if bot_token and chat_id:
        return bot_token, chat_id
    try:
        with open(cfg_path, 'r', encoding='utf-8') as f:
            cfg = json.load(f)
            return cfg.get('bot_token'), str(cfg.get('chat_id'))
    except FileNotFoundError:
        return None, None

BOT_TOKEN, CHAT_ID = load_telegram_config()
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

def send_telegram_message(text: str, parse_mode: str = "Markdown"):
    if not BOT_TOKEN or not CHAT_ID:
        print("[TG DISABLED] >>", text)
        return False
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    try:
        resp = requests.post(TELEGRAM_API.format(token=BOT_TOKEN), data=payload, timeout=10)
        return resp.status_code == 200
    except Exception as e:
        print(f"[TG ERROR] {e}")
        return False

def format_multi_coin_wait_message(unlock_time_dt, amount, coins=None):
    if coins is None:
        coins = ['Pi']
    coin_lines = []
    for c in coins:
        icon = 'π' if c.lower() == 'pi' else ('₿' if c.lower()=='btc' else 'Ξ' if c.lower()=='eth' else '◈')
        coin_lines.append(f"{icon} *{c}*")
    coin_block = "   ".join(coin_lines)
    dt_str = unlock_time_dt.strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"{coin_block}\n\n"
        f"⏳ *MENUNGGU UNLOCK*\n\n"
        f"Jadwal: {dt_str}\n"
        f"Amount: {float(amount):.7f} Pi"
    )
    return msg

def notify_tx_result(success: bool, info: str, amount=None, balance_id=None):
    status = "✅ TRANSAKSI BERHASIL" if success else "❌ TRANSAKSI GAGAL"
    lines = [f"*{status}*"]
    if amount is not None:
        lines.append(f"Amount: {float(amount):.7f} Pi")
    if balance_id:
        lines.append(f"Balance ID: `{balance_id}`")
    lines.append(f"Info: `{info}`")
    msg = "\n".join(lines)
    send_telegram_message(msg)

def send_summary_notification(success_count, fail_count, total, unlock_time=None, balance_id=None, amount=None):
    icon = "✅" if success_count > 0 and fail_count == 0 else "⚠️" if success_count > 0 else "❌"
    lines = [
        f"{icon} *RINGKASAN TRANSAKSI SPAM*",
        f"Total Percobaan: {total}",
        f"Berhasil: {success_count}",
        f"Gagal: {fail_count}",
    ]
    if amount:
        lines.append(f"Amount: {float(amount):.7f} Pi")
    if unlock_time:
        lines.append(f"Jadwal Unlock: {unlock_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if balance_id:
        lines.append(f"Balance ID: `{balance_id}`")
    msg = "\n".join(lines)
    send_telegram_message(msg)

# ==============================
# Cek & Claimable
# ==============================
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
                if time_diff <= 0:
                    can_claim_now = True
            elif predicate == {"unconditional": True}:
                can_claim_now = True
            if can_claim_now:
                print(f"Ready to claim: {balance_id}")

def nextunlock():
    global UNLOCK_TIME, UNLOCK_ID, UNLOCK_BALANCE, REMAIN_BALANCE, UNLOCKS
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
                        UNLOCK_BALANCE = str(amt)
                        UNLOCKS = True
        if UNLOCK_TIME:
            send_telegram_message(format_multi_coin_wait_message(UNLOCK_TIME, UNLOCK_BALANCE, coins=['Pi']))
    else:
        print("No claimable balances found.")

# ==============================
# Fungsi transaksi
# ==============================
def reload():
    return server.load_account(TX_PAYER_AD)

def account_sequence():
    account = server.load_account(TX_PAYER_AD)
    return account.sequence

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

def countdown():
    global UNLOCK_TIME, ACCOUNT, SEQUENCE
    while True:
        now = datetime.now(timezone.utc)
        if UNLOCK_TIME is not None:
            time_diff = (UNLOCK_TIME - now).total_seconds()
            if 1.8 <= time_diff <= 2.2:
                print("[PREPARE] Building transaction ~2s before unlock...")
                ACCOUNT = reload()
                SEQUENCE = ACCOUNT.sequence
                countdown.tx_prepared = transaction_builder_(local_acc=ACCOUNT)
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

def start_spamming(parallel_count=50):
    global SEQUENCE, ACCOUNT
    threads = []
    success_count = 0
    fail_count = 0
    print(f"[SPAM] Launching {parallel_count} spam transactions...")

    lock = threading.Lock()

    def claim_and_send_with_counter(i, seq):
        nonlocal success_count, fail_count
        LOCAL_ACCOUNT = ACCOUNT
        LOCAL_ACCOUNT.sequence = seq
        transaction_b = transaction_builder_(local_acc=LOCAL_ACCOUNT)
        try:
            response = server.submit_transaction(transaction_b)
            print(f"[PASS] Transaction -{i}- Submitted")
            with lock:
                success_count += 1
        except Exception as e:
            print(f"[ERROR] Transaction -{i}- Error: {e}")
            with lock:
                fail_count += 1

    for i in range(parallel_count):
        seq = SEQUENCE + i
        t = threading.Thread(target=claim_and_send_with_counter, args=(i, seq))
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    print("[SPAM] Semua transaksi selesai.")
    send_summary_notification(
        success_count,
        fail_count,
        total=parallel_count,
        unlock_time=UNLOCK_TIME,
        balance_id=UNLOCK_ID,
        amount=UNLOCK_BALANCE
    )

# ==============================
# Eksekusi utama
# ==============================
print("[CLAIMABLE] Checking claimable balances...")
checking_claimable_balances()
print("[CLAIMABLE] Checking for next unlock time...")
nextunlock()

if UNLOCK_TIME:
    print(f"[CLAIMABLE] Next unlock time: {UNLOCK_TIME} for Balance ID: {UNLOCK_ID} - {UNLOCK_BALANCE} Pi")
else:
    print("No claimable balances found.")

print("[CLAIMABLE] Starting countdown...")
countdown()

if UNLOCKS and hasattr(countdown, "tx_prepared"):
    try:
        print("[SUBMIT] Submitting prepared transaction...")
        response = server.submit_transaction(countdown.tx_prepared)
        print("[PASS] Main Transaction Submitted")
        notify_tx_result(True, str(response), amount=UNLOCK_BALANCE, balance_id=UNLOCK_ID)
        ACCOUNT = reload()
        SEQUENCE = ACCOUNT.sequence
        start_spamming(parallel_count=50)
    except Exception as e:
        print(f"[ERROR] Failed to submit main transaction: {e}")
        notify_tx_result(False, str(e), amount=UNLOCK_BALANCE, balance_id=UNLOCK_ID)
else:
    print("No transaction prepared or unlock not ready.")

print(f"[CLAIMABLE] Total: {total_claimable:.6f} Pi")
