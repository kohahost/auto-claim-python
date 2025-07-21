from bip_utils import Bip39SeedGenerator, Bip32Slip10Ed25519
from stellar_sdk import Keypair, StrKey

# Masukkan mnemonic kamu di sini:
mnemonic = "wide reveal among fiscal figure cycle predict hour shoe salon keep leg recipe home craft surface supreme sort zero knife sunny room comfort leaf"

# Generate seed dan derive private key dari path Pi Network
seed = Bip39SeedGenerator(mnemonic).Generate()
bip32_ctx = Bip32Slip10Ed25519.FromSeed(seed)
derived = bip32_ctx.DerivePath("m/44'/314159'/0'")
priv_key_bytes = derived.PrivateKey().Raw().ToBytes()

# Buat secret & public key Stellar (untuk Pi Network)
secret_key = StrKey.encode_ed25519_secret_seed(priv_key_bytes)
keypair = Keypair.from_secret(secret_key)
public_key = keypair.public_key

# Tampilkan hasil
print(f'TX_PAYER = "{secret_key}"')  # Secret Key → untuk script
print(f'Public Key (alamat wallet) = {public_key}')  # Untuk menerima Pi
