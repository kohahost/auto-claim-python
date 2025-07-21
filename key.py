from bip_utils import Bip39SeedGenerator, Bip32Slip10Ed25519
from stellar_sdk import Keypair, StrKey, Server

mnemonic = "wide reveal among fiscal figure cycle predict hour shoe salon keep leg recipe home craft surface supreme sort zero knife sunny room comfort leaf"

# Generate keys
seed = Bip39SeedGenerator(mnemonic).Generate()
bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed)
derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
priv_key_bytes = derived.PrivateKey().Raw().ToBytes()

secret_key = StrKey.encode_ed25519_secret_seed(priv_key_bytes)
kp = Keypair.from_secret(secret_key)
public_key = kp.public_key

# Connect to Pi mainnet
server = Server("https://api.mainnet.minepi.com")

# Load account & get balance
try:
    account = server.load_account(public_key)
    balances = account.balances
    print(f"\nPublic Key: {public_key}")
    print(f"Secret Key: {secret_key}")
    print("Balances:")
    for b in balances:
        print(f" - {b['asset_type']}: {b['balance']}")
except Exception as e:
    print("Akun belum aktif di jaringan atau error:", e)
