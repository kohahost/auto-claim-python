# -*- coding: utf-8 -*-

# =============================================================================
# IMPORTS
# =============================================================================
from bip_utils import Bip32Slip10Ed25519, Bip39SeedGenerator, Bip39MnemonicValidator
from stellar_sdk import Keypair, StrKey, Server, TransactionBuilder, Asset
from datetime import datetime, timezone, timedelta
import time
import threading
import logging
import uuid
from telegram import Update
from telegram.constants import ParseMode
# PERUBAHAN DI SINI
from telegram.ext import filters, Updater, CommandHandler, MessageHandler, ConversationHandler, CallbackContext
# AKHIR PERUBAHAN

# =============================================================================
# --- KONFIGURASI BOT & JARINGAN ---
# Ganti nilai di bawah ini
# =============================================================================

# -- Konfigurasi Telegram --
TELEGRAM_BOT_TOKEN = "8473474866:AAGkldHvcmJkkqbt-FdrO46kX-K4MVtLa9A"
# ID Telegram Anda, agar hanya Anda yang bisa menggunakan bot ini
ADMIN_CHAT_ID = 7890743177  # GANTI DENGAN CHAT ID ADMIN (WAJIB DIISI, misal: 123456789)

# -- Konfigurasi Jaringan & Transaksi --
NETWORK_PASSPHRASSE = "Pi Network"
HORIZON_URL = "http://4.194.35.14:31401"
BASE_FEE = 40000000

# -- Konfigurasi Akun --
TX_PAYER_SECRET = "SAVB25TPJNM7O5EO46R6TRAKIFLJDWSWWIP5WPKMU2Z72LZSH3QUWHGM"
DESTINATION_ADDRESS = "GA2CXP2KK2PANC3JDEZLRKONYZBWXMTHY3N235QNJQFGGTJRUMPNM75X"

# -- Konfigurasi Strategi --
PARALLEL_COUNT = 50
SEND_OFFSET_SECONDS = 0.2
REMAIN_BALANCE = 1

# =============================================================================
# --- PENGATURAN LOGGING & KUNCI (JANGAN DIUBAH) ---
# =============================================================================
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

KP_TX_PAYER = Keypair.from_secret(TX_PAYER_SECRET)
TX_PAYER_AD = KP_TX_PAYER.public_key

# State untuk ConversationHandler
WAITING_MNEMONIC = 0

# Dictionary untuk menyimpan tugas-tugas klaim yang aktif
ACTIVE_TASKS = {}
task_lock = threading.Lock()

# =============================================================================
# --- KELAS UNTUK MENGELOLA SETIAP PROSES KLAIM ---
# =============================================================================
class ClaimProcess:
    """Membungkus semua logika dan status untuk satu proses klaim."""
    def __init__(self, context: CallbackContext, mnemonic: str):
        self.context = context
        self.mnemonic = mnemonic
        self.task_id = str(uuid.uuid4())[:8] # ID unik untuk setiap tugas
        self.pi_keypair = None
        self.server = Server(HORIZON_URL)
        
        # Atribut status
        self.status = "Initializing"
        self.unlock_id = None
        self.unlock_time = None
        self.unlock_balance = None
        self.success_notified = False
        
    def _send_message(self, text: str, **kwargs):
        """Helper untuk mengirim pesan ke admin."""
        self.context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, **kwargs)

    def _get_keys(self):
        """Memvalidasi mnemonic dan mendapatkan kunci."""
        self.status = "Validating mnemonic"
        if not Bip39MnemonicValidator().IsValid(self.mnemonic):
            raise ValueError("Mnemonic tidak valid.")
        
        seed_bytes = Bip39SeedGenerator(self.mnemonic).Generate()
        bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed_bytes)
        derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
        priv_key_bytes = derived.PrivateKey().Raw().ToBytes()
        secret = StrKey.encode_ed25519_secret_seed(priv_key_bytes)
        self.pi_keypair = Keypair.from_secret(secret)

    def _find_next_unlock(self):
        """Mencari jadwal unlock berikutnya."""
        self.status = "Searching for unlock"
        account_id = self.pi_keypair.public_key
        claimables = self.server.claimable_balances().for_claimant(account_id).limit(20).call()
        
        next_unlock_time = None
        next_unlock_details = {}

        if not claimables or not claimables["_embedded"]["records"]:
            return False

        for c in claimables["_embedded"]["records"]:
            for claimant in c["claimants"]:
                if claimant["destination"] != account_id: continue
                
                predicate = claimant.get("predicate", {})
                if "not" in predicate and "abs_before" in predicate["not"]:
                    unlock_utc = datetime.fromisoformat(predicate["not"]["abs_before"].replace("Z", "+00:00"))
                    if unlock_utc > datetime.now(timezone.utc):
                        if next_unlock_time is None or unlock_utc < next_unlock_time:
                            next_unlock_time = unlock_utc
                            next_unlock_details = {"id": c["id"], "amount": c["amount"], "time": unlock_utc}

        if next_unlock_details:
            self.unlock_id = next_unlock_details["id"]
            self.unlock_time = next_unlock_details["time"]
            amount_to_send = float(next_unlock_details["amount"]) - REMAIN_BALANCE
            self.unlock_balance = f"{amount_to_send:.7f}"
            return True
        return False
        
    def _submit_single_transaction(self, tx, tx_num):
        """Mengirim satu transaksi dan menangani notifikasi."""
        try:
            response = self.server.submit_transaction(tx)
            logger.info(f"Task {self.task_id}: Transaksi #{tx_num} Berhasil!")
            if not self.success_notified:
                self.success_notified = True
                message = (f"✅ *TRANSAKSI BERHASIL (Tugas: {self.task_id})*\n\n"
                           f"Akun: `...{self.pi_keypair.public_key[-5:]}`\n"
                           f"Amount: `{self.unlock_balance}` Pi\n"
                           f"Hash: `{response['hash']}`")
                self._send_message(message, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            if tx_num == 0 and not self.success_notified:
                self.success_notified = True # Mencegah notif gagal berulang
                logger.error(f"Task {self.task_id}: Transaksi #{tx_num} Gagal: {e}")
                message_gagal = (f"❌ *TRANSAKSI GAGAL (Tugas: {self.task_id})*\n\n"
                                 f"Akun: `...{self.pi_keypair.public_key[-5:]}`\n"
                                 f"Error: `{str(e)}`")
                self._send_message(message_gagal, parse_mode=ParseMode.MARKDOWN_V2)

    def run(self):
        """Metode utama yang menjalankan seluruh alur proses klaim."""
        try:
            self._send_message(f"Memulai tugas baru, ID: `{self.task_id}`", parse_mode=ParseMode.MARKDOWN_V2)
            self._get_keys()
            short_pk = f"...{self.pi_keypair.public_key[-5:]}"
            self._send_message(f"✅ Tugas `{self.task_id}`: Validasi berhasil untuk akun `{short_pk}`.", parse_mode=ParseMode.MARKDOWN_V2)
            
            if not self._find_next_unlock():
                self.status = "Finished (No Unlock)"
                self._send_message(f"ℹ️ Tugas `{self.task_id}`: Tidak ada jadwal unlock ditemukan untuk akun ini.", parse_mode=ParseMode.MARKDOWN_V2)
                return

            self.status = f"Waiting for unlock at {self.unlock_time.strftime('%H:%M:%S')}"
            self._send_message(
                f"🎯 Tugas `{self.task_id}`: Target ditemukan!\n\n"
                f"Waktu: `{self.unlock_time.strftime('%Y-%m-%d %H:%M:%S UTC')}`\n"
                f"Amount: `{self.unlock_balance}` Pi",
                parse_mode=ParseMode.MARKDOWN_V2
            )

            # Persiapan transaksi
            fee_payer_account = self.server.load_account(TX_PAYER_AD)
            base_sequence = fee_payer_account.sequence
            prepared_txs = []
            for i in range(PARALLEL_COUNT):
                fee_payer_account.sequence = base_sequence + i
                tx = TransactionBuilder(
                        source_account=fee_payer_account, network_passphrase=NETWORK_PASSPHRASSE, base_fee=BASE_FEE
                    ).append_claim_claim_balance_op(
                        balance_id=self.unlock_id, source=self.pi_keypair.public_key
                    ).append_payment_op(
                        destination=DESTINATION_ADDRESS, amount=self.unlock_balance, asset=Asset.native(), source=self.pi_keypair.public_key
                    ).set_timeout(30).build()
                tx.sign(self.pi_keypair)
                tx.sign(KP_TX_PAYER)
                prepared_txs.append(tx)

            # Countdown
            while True:
                time_diff = (self.unlock_time - datetime.now(timezone.utc)).total_seconds()
                if time_diff <= SEND_OFFSET_SECONDS:
                    self.status = "Executing"
                    threads = [threading.Thread(target=self._submit_single_transaction, args=(tx, i)) for i, tx in enumerate(prepared_txs)]
                    self._send_message(f"🚀 Tugas `{self.task_id}`: Meluncurkan {len(threads)} transaksi!", parse_mode=ParseMode.MARKDOWN_V2)
                    for t in threads: t.start()
                    for t in threads: t.join()
                    break
                time.sleep(0.1)

            self.status = "Finished"

        except Exception as e:
            self.status = f"Error: {e}"
            logger.error(f"Error fatal di tugas {self.task_id}: {e}", exc_info=True)
            self._send_message(f"🔥 *ERROR KRITIS (Tugas: {self.task_id})*\n\n`{str(e)}`", parse_mode=ParseMode.MARKDOWN_V2)
        
        finally:
            # Hapus tugas dari daftar aktif setelah selesai
            with task_lock:
                if self.task_id in ACTIVE_TASKS:
                    del ACTIVE_TASKS[self.task_id]
            logger.info(f"Tugas {self.task_id} telah selesai dan dihapus dari daftar aktif.")


# =============================================================================
# --- HANDLER UNTUK BOT TELEGRAM ---
# =============================================================================

def start(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID: return
    update.message.reply_text(
        "Bot multi-claim Pi Network aktif.\n"
        "/claim - Menambahkan tugas klaim baru.\n"
        "/status - Melihat semua tugas aktif."
    )

def claim_command(update: Update, context: CallbackContext) -> int:
    if update.effective_user.id != ADMIN_CHAT_ID: return ConversationHandler.END
    update.message.reply_text(
        "Silakan kirim Passphrase (24 kata) untuk akun yang ingin Anda proses.\n"
        "Pesan akan otomatis dihapus.\n\n"
        "Kirim /cancel untuk membatalkan."
    )
    return WAITING_MNEMONIC

def receive_mnemonic(update: Update, context: CallbackContext) -> int:
    mnemonic = update.message.text
    context.bot.delete_message(chat_id=update.message.chat_id, message_id=update.message.message_id)

    # Buat instance ClaimProcess baru dan jalankan di thread
    claim_process = ClaimProcess(context, mnemonic)
    
    with task_lock:
        ACTIVE_TASKS[claim_process.task_id] = claim_process
    
    thread = threading.Thread(target=claim_process.run)
    thread.start()
    
    update.message.reply_text(f"Tugas klaim baru dengan ID `{claim_process.task_id}` telah dimulai di latar belakang.", parse_mode=ParseMode.MARKDOWN_V2)
    return ConversationHandler.END

def status_command(update: Update, context: CallbackContext) -> None:
    if update.effective_user.id != ADMIN_CHAT_ID: return
    
    with task_lock:
        if not ACTIVE_TASKS:
            update.message.reply_text("Tidak ada tugas klaim yang sedang aktif.")
            return

        message = "*Tugas Aktif:*\n\n"
        for task_id, task in ACTIVE_TASKS.items():
            pk_short = f"...{task.pi_keypair.public_key[-5:]}" if task.pi_keypair else "N/A"
            message += (f"🔹 *ID:* `{task_id}`\n"
                        f"   *Akun:* `{pk_short}`\n"
                        f"   *Status:* {task.status}\n\n")

    update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN_V2)


def cancel(update: Update, context: CallbackContext) -> int:
    update.message.reply_text("Proses penambahan tugas dibatalkan.")
    return ConversationHandler.END

def main() -> None:
    if TELEGRAM_BOT_TOKEN == "GANTI_DENGAN_TOKEN_BOT_ANDA" or ADMIN_CHAT_ID == 0:
        print("!!! KESALAHAN: Harap isi TELEGRAM_BOT_TOKEN dan ADMIN_CHAT_ID di dalam skrip.")
        return

    # Di v20, Updater diganti ApplicationBuilder
    from telegram.ext import Application
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('claim', claim_command)],
        states={
            # PERUBAHAN DI SINI
            WAITING_MNEMONIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_mnemonic)],
            # AKHIR PERUBAHAN
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(conv_handler)
    
    print("Bot multi-claim sedang berjalan... Tekan Ctrl+C untuk berhenti.")
    application.run_polling()


if __name__ == '__main__':
    main()
