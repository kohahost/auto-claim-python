# -*- coding: utf-8 -*-

# =============================================================================
# IMPORTS
# =============================================================================
from bip_utils import Bip32Slip10Ed25519, Bip39SeedGenerator, Bip39MnemonicValidator
from stellar_sdk import Keypair, StrKey, Server, TransactionBuilder, Asset
from datetime import datetime, timezone, timedelta
import time
import threading
import requests

# =============================================================================
# --- KONFIGURASI ---
# Ganti nilai di bawah ini sesuai dengan kebutuhan Anda
# =============================================================================

# -- Konfigurasi Notifikasi Telegram --
# Dapatkan dari @BotFather di Telegram
TELEGRAM_BOT_TOKEN = "8473474866:AAGkldHvcmJkkqbt-FdrO46kX-K4MVtLa9A" 
# Dapatkan dari @userinfobot di Telegram
TELEGRAM_CHAT_ID = "7890743177"

# -- Konfigurasi Jaringan & Transaksi --
NETWORK_PASSPHRASE = "Pi Network"
HORIZON_URL = "http://4.194.35.14:31401"
BASE_FEE = 40000000 # Biaya dasar per operasi

# -- Konfigurasi Akun --
# Rahasia (Secret Key) akun yang akan membayar biaya transaksi
TX_PAYER_SECRET = "SAVB25TPJNM7O5EO46R6TRAKIFLJDWSWWIP5WPKMU2Z72LZSH3QUWHGM"
# Alamat (Public Key) tujuan pengiriman Pi setelah di-klaim
DESTINATION_ADDRESS = "GA2CXP2KK2PANC3JDEZLRKONYZBWXMTHY3N235QNJQFGGTJRUMPNM75X"

# -- Konfigurasi Strategi --
# Jumlah transaksi yang akan dikirim secara paralel untuk memaksimalkan peluang
PARALLEL_COUNT = 50 
# Berapa detik sebelum unlock transaksi akan dikirim (untuk antisipasi latensi)
SEND_OFFSET_SECONDS = 0.2
# Sisa saldo yang akan ditinggalkan di claimable balance (misal: 1 Pi)
REMAIN_BALANCE = 1

# =============================================================================
# --- VARIABEL GLOBAL & KUNCI (JANGAN DIUBAH) ---
# =============================================================================
KP_TX_PAYER = Keypair.from_secret(TX_PAYER_SECRET)
TX_PAYER_AD = KP_TX_PAYER.public_key
your_timezone = timezone(timedelta(hours=1)) # Sesuaikan jika zona waktu Anda berbeda

# Status untuk mengontrol notifikasi agar tidak spam
SUCCESS_NOTIFIED = False
notification_lock = threading.Lock()

# Variabel untuk menyimpan detail unlock
UNLOCK_ID = None
UNLOCK_TIME = None
UNLOCK_BALANCE = None
UNLOCKS = False

# =============================================================================
# --- FUNGSI-FUNGSI UTAMA ---
# =============================================================================

def send_telegram_notification(message):
    """Mengirim pesan notifikasi ke bot Telegram dengan format MarkdownV2."""
    if "GANTI_DENGAN" in TELEGRAM_BOT_TOKEN or "GANTI_DENGAN" in TELEGRAM_CHAT_ID:
        print("[PERINGATAN TELEGRAM] Token Bot atau Chat ID belum diatur. Melewatkan notifikasi.")
        return

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    # Escape karakter khusus untuk MarkdownV2
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    escaped_message = "".join(['\\' + char if char in escape_chars else char for char in message])

    payload = {'chat_id': TELEGRAM_CHAT_ID, 'text': escaped_message, 'parse_mode': 'MarkdownV2'}
    
    try:
        response = requests.post(api_url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"[ERROR TELEGRAM] Gagal mengirim notifikasi: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"[ERROR TELEGRAM] Terjadi kesalahan saat koneksi: {e}")

def get_pi_keys_from_mnemonic(mnemonic):
    """Mendapatkan kunci Stellar dari mnemonic Pi."""
    if not Bip39MnemonicValidator().IsValid(mnemonic):
        raise ValueError("Mnemonic tidak valid.")
    seed_bytes = Bip39SeedGenerator(mnemonic).Generate()
    bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed_bytes)
    derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
    priv_key_bytes = derived.PrivateKey().Raw().ToBytes()
    secret = StrKey.encode_ed25519_secret_seed(priv_key_bytes)
    keypair = Keypair.from_secret(secret)
    return keypair

def find_next_unlock(claimables, account_id):
    """Mencari dan menetapkan jadwal unlock berikutnya dari daftar claimable balances."""
    global UNLOCK_TIME, UNLOCK_ID, UNLOCK_BALANCE, UNLOCKS
    
    next_unlock_time = None
    next_unlock_details = {}

    if not claimables or not claimables["_embedded"]["records"]:
        print("Tidak ada claimable balance ditemukan.")
        UNLOCKS = False
        return

    for c in claimables["_embedded"]["records"]:
        for claimant in c["claimants"]:
            if claimant["destination"] != account_id:
                continue
            
            predicate = claimant.get("predicate", {})
            if "not" in predicate and "abs_before" in predicate["not"]:
                unlock_utc_str = predicate["not"]["abs_before"]
                unlock_utc = datetime.fromisoformat(unlock_utc_str.replace("Z", "+00:00"))

                # Hanya proses unlock yang akan datang
                if unlock_utc > datetime.now(timezone.utc):
                    if next_unlock_time is None or unlock_utc < next_unlock_time:
                        next_unlock_time = unlock_utc
                        next_unlock_details = {
                            "id": c["id"],
                            "amount": c["amount"],
                            "time_utc": unlock_utc
                        }

    if next_unlock_details:
        UNLOCKS = True
        UNLOCK_ID = next_unlock_details["id"]
        UNLOCK_TIME = next_unlock_details["time_utc"]
        amount_to_send = float(next_unlock_details["amount"]) - REMAIN_BALANCE
        UNLOCK_BALANCE = f"{amount_to_send:.7f}"
    else:
        print("Tidak ada jadwal unlock di masa depan yang ditemukan.")
        UNLOCKS = False

def build_transaction(source_account, claim_id, destination, amount, keypair):
    """Membangun satu transaksi 'claim and send'."""
    tx = (
        TransactionBuilder(source_account=source_account, network_passphrase=NETWORK_PASSPHRASE, base_fee=BASE_FEE)
        .append_claim_claimable_balance_op(balance_id=claim_id, source=keypair.public_key)
        .append_payment_op(destination=destination, amount=amount, asset=Asset.native(), source=keypair.public_key)
        .set_timeout(30)
        .build()
    )
    tx.sign(keypair)
    tx.sign(KP_TX_PAYER)
    return tx

def submit_single_transaction(tx, tx_num, server):
    """Fungsi target untuk setiap thread, mengirim satu transaksi."""
    global SUCCESS_NOTIFIED
    try:
        response = server.submit_transaction(tx)
        print(f"[PASS] Transaksi #{tx_num} Berhasil Dikirim!")
        with notification_lock:
            if not SUCCESS_NOTIFIED:
                message = (f"✅ *TRANSAKSI BERHASIL (No. {tx_num})*\n\n"
                           f"Amount: {UNLOCK_BALANCE} Pi\n"
                           f"Hash: `{response['hash']}`")
                send_telegram_notification(message)
                SUCCESS_NOTIFIED = True
    except Exception as e:
        # Hanya tampilkan error untuk transaksi pertama agar log tidak penuh
        if tx_num == 0:
            print(f"[ERROR] Transaksi #{tx_num} Gagal: {e}")
            with notification_lock:
                # Kirim notif gagal hanya jika belum ada yang sukses
                if not SUCCESS_NOTIFIED:
                    error_message = str(e)
                    # Coba ekstrak pesan error yang lebih bersih
                    if "op_no_trust" in error_message:
                        error_clean = "Tujuan belum mengaktifkan trustline Pi."
                    elif "tx_bad_seq" in error_message:
                        error_clean = "Sequence number salah (tx_bad_seq)."
                    else:
                        error_clean = "Cek log untuk detail."
                    
                    message_gagal = (f"❌ *TRANSAKSI GAGAL*\n\n"
                                     f"Error Utama: `{error_clean}`")
                    send_telegram_notification(message_gagal)
                    SUCCESS_NOTIFIED = True # Set agar notifikasi gagal tidak dikirim lagi

def launch_transactions(transactions, server):
    """Meluncurkan semua transaksi yang sudah disiapkan secara serentak."""
    threads = []
    print(f"\n[LAUNCH] Meluncurkan {len(transactions)} transaksi secara serentak...")
    for i, tx in enumerate(transactions):
        t = threading.Thread(target=submit_single_transaction, args=(tx, i, server))
        t.start()
        threads.append(t)
    
    for t in threads:
        t.join()
    print("[FINISH] Semua upaya pengiriman transaksi telah selesai.")

# =============================================================================
# --- ALUR EKSEKUSI UTAMA ---
# =============================================================================

if __name__ == "__main__":
    try:
        # 1. Setup Awal
        mnemonic = input("Masukkan Passphrase (24 kata) Anda: ")
        pi_keypair = get_pi_keys_from_mnemonic(mnemonic)
        print(f"\n[INFO] Alamat Pi Anda: {pi_keypair.public_key}")
        
        server = Server(HORIZON_URL)

        # 2. Mencari Jadwal Unlock
        print("[INIT] Mencari claimable balances...")
        claimables_data = server.claimable_balances().for_claimant(pi_keypair.public_key).limit(20).call()
        find_next_unlock(claimables_data, pi_keypair.public_key)

        if UNLOCKS:
            unlock_time_local = UNLOCK_TIME.astimezone(your_timezone)
            print(f"[TARGET] Ditemukan unlock berikutnya pada: {unlock_time_local.strftime('%Y-%m-%d %H:%M:%S %Z')}")
            print(f"[TARGET] ID Balance: {UNLOCK_ID}")
            print(f"[TARGET] Amount akan dikirim: {UNLOCK_BALANCE} Pi")
            send_telegram_notification(f"⏳ *MENUNGGU UNLOCK*\n\nJadwal: {unlock_time_local.strftime('%Y-%m-%d %H:%M:%S')}\nAmount: {UNLOCK_BALANCE} Pi")

            # 3. Persiapan Transaksi (sebelum countdown)
            print(f"\n[PREPARE] Mempersiapkan {PARALLEL_COUNT} transaksi...")
            fee_payer_account = server.load_account(TX_PAYER_AD)
            base_sequence = fee_payer_account.sequence
            
            prepared_transactions = []
            for i in range(PARALLEL_COUNT):
                fee_payer_account.sequence = base_sequence + i
                tx = build_transaction(
                    source_account=fee_payer_account,
                    claim_id=UNLOCK_ID,
                    destination=DESTINATION_ADDRESS,
                    amount=UNLOCK_BALANCE,
                    keypair=pi_keypair
                )
                prepared_transactions.append(tx)
            
            print("[PREPARE] Semua transaksi berhasil dibuat. Memulai countdown.")

            # 4. Countdown dan Peluncuran
            while True:
                now = datetime.now(timezone.utc)
                time_diff = (UNLOCK_TIME - now).total_seconds()
                
                if time_diff <= SEND_OFFSET_SECONDS:
                    launch_transactions(prepared_transactions, server)
                    break
                
                if time_diff < 10: # Tampilkan countdown hanya jika sudah dekat
                    # \r membawa kursor ke awal baris, end='' mencegah baris baru
                    print(f"\r[COUNTDOWN] {time_diff:.2f} detik tersisa...", end="")
                
                time.sleep(0.02) # Cek setiap 20 milidetik
        else:
            send_telegram_notification("ℹ️ *INFO*\n\nTidak ada jadwal unlock di masa depan yang ditemukan untuk akun ini.")

    except ValueError as e:
        print(f"\n[FATAL ERROR] {e}")
        send_telegram_notification(f"🔥 *ERROR KRITIS*\n\nTerjadi kesalahan validasi: `{str(e)}`")
    except Exception as e:
        print(f"\n[FATAL ERROR] Terjadi kesalahan tak terduga: {e}")
        send_telegram_notification(f"🔥 *ERROR KRITIS*\n\nTerjadi kesalahan tak terduga: `{str(e)}`")

    print("\n[END] Skrip telah selesai dieksekusi.")
