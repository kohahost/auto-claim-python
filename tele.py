import os
import time
import threading
import requests
from datetime import datetime, timezone
from stellar_sdk import Keypair, Network, TransactionBuilder, Server
from bip_utils import Bip39SeedGenerator, Bip32Slip10Ed25519
from dotenv import load_dotenv

# =========================
# 🔧 KONFIGURASI DASAR
# =========================
load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
HORIZON_URL = "http://4.194.35.14:31401"
NETWORK_PASSPHRASE = "Pi Network"
TX_PAYER_MNEMONIC = os.getenv("TX_PAYER_MNEMONIC")

server = Server(HORIZON_URL)

# =========================
# 🔧 FUNGSI UTAMA
# =========================
def mnemonic_to_keypair(mnemonic: str):
    seed = Bip39SeedGenerator(mnemonic).Generate()
    bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed)
    derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
    keypair = Keypair.from_secret(derived.PrivateKey().Raw().ToHex())
    return keypair

def send_telegram(msg):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[WARN] Telegram not configured.")
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        requests.post(url, data=data, timeout=10)
    except Exception as e:
        print(f"[Telegram Error] {e}")

def get_claimable_balances(address):
    try:
        url = f"{HORIZON_URL}/claimable_balances?claimant={address}"
        res = requests.get(url, timeout=10).json()
        return res["_embedded"]["records"]
    except Exception as e:
        print(f"[ERROR] Fetch claimables: {e}")
        return []

def next_unlock(balances):
    """Ambil balance dengan unlock_time terdekat"""
    unlocks = []
    for b in balances:
        cond = b["claimants"][0]["predicate"]
        if "abs_before" in cond:
            t = datetime.fromisoformat(cond["abs_before"].replace("Z", "+00:00"))
            unlocks.append((b["id"], float(b["amount"]), t))
    if not unlocks:
        return None
    unlocks.sort(key=lambda x: x[2])
    return unlocks[0]  # (id, amount, unlock_time)

# =========================
# 🚀 PERSIAPAN TRANSAKSI
# =========================
def build_transaction(kp_sender, kp_payer, balance_id, dest, seq):
    account = server.load_account(kp_sender.public_key)
    tx = (
        TransactionBuilder(source_account=account, network_passphrase=NETWORK_PASSPHRASE, base_fee=100)
        .append_claim_claimable_balance_op(balance_id)
        .append_payment_op(destination=dest, amount="0.000001", asset_code="Pi")
        .set_timeout(30)
        .build()
    )
    tx.sign(kp_sender)
    tx.sign(kp_payer)
    return tx

def submit_transaction(tx):
    try:
        res = server.submit_transaction(tx)
        return True, res["hash"]
    except Exception as e:
        return False, str(e)

# =========================
# ⏳ COUNTDOWN PRESISI
# =========================
def countdown_to_unlock(unlock_time, kp_sender, kp_payer, balance_id, dest):
    print(f"[COUNTDOWN] Menuju unlock: {unlock_time.isoformat()} UTC")

    tx_prepared = None
    last_sec = None

    while True:
        now = datetime.now(timezone.utc)
        diff = (unlock_time - now).total_seconds()

        # tampilkan waktu setiap detik
        if int(diff) != last_sec and diff > 1:
            print(f"[WAIT] {diff:.3f}s tersisa...")
            last_sec = int(diff)

        # Siapkan 1 detik sebelum unlock
        if 0.9 <= diff <= 1.1 and not tx_prepared:
            print("[PREPARE] Build TX (1s before unlock)")
            tx_prepared = build_transaction(kp_sender, kp_payer, balance_id, dest, None)

        # Kirim tepat di detik unlock
        elif 0 <= diff <= 0.05:
            print(f"[SEND] Tepat waktu unlock ({diff:.3f}s delay)")
            success, info = submit_transaction(tx_prepared)
            msg = (
                f"✅ *Transaksi Berhasil!*\n\n"
                f"Balance ID: `{balance_id}`\n"
                f"Hash: `{info}`\n"
                f"Waktu: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
                if success
                else f"❌ *Transaksi Gagal!*\nError: `{info}`"
            )
            send_telegram(msg)
            break

        elif diff < -1:
            print("[INFO] Unlock time lewat.")
            break

        time.sleep(0.005)

# =========================
# 🧩 MAIN PROGRAM
# =========================
def main():
    # Ambil mnemonic pengirim utama
    try:
        with open("mnemonics.txt", "r") as f:
            mnemonic_sender = f.readline().strip().replace('"', "")
    except Exception as e:
        print(f"[ERROR] Tidak bisa baca mnemonics.txt: {e}")
        return

    kp_sender = mnemonic_to_keypair(mnemonic_sender)
    kp_payer = mnemonic_to_keypair(TX_PAYER_MNEMONIC)

    balances = get_claimable_balances(kp_sender.public_key)
    next_item = next_unlock(balances)

    if not next_item:
        print("[INFO] Tidak ada saldo yang menunggu unlock.")
        return

    balance_id, amount, unlock_time = next_item
    msg_wait = (
        f"⏳ *MENUNGGU UNLOCK*\n\n"
        f"Jadwal: `{unlock_time.strftime('%Y-%m-%d %H:%M:%S')}` UTC\n"
        f"Amount: *{amount} Pi*"
    )
    send_telegram(msg_wait)

    countdown_to_unlock(unlock_time, kp_sender, kp_payer, balance_id, kp_sender.public_key)

# =========================
# 🏁 JALANKAN
# =========================
if __name__ == "__main__":
    main()
