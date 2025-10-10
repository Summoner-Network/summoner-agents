from Crypto.PublicKey import RSA
from Crypto.Signature import pkcs1_15
from Crypto.Hash import SHA256
import base64

def generate_key_pair(private_key_path="private_key.pem", public_key_path="public_key.pem"):
    """Generates a new RSA key pair and saves them to PEM files."""
    key = RSA.generate(2048)
    private_key = key.export_key()
    with open(private_key_path, "wb") as f:
        f.write(private_key)

    public_key = key.publickey().export_key()
    with open(public_key_path, "wb") as f:
        f.write(public_key)
    print(f"Keys generated and saved to {private_key_path} and {public_key_path}")

def load_private_key(path="private_key.pem"):
    """Loads an RSA private key from a file."""
    with open(path, "rb") as f:
        return RSA.import_key(f.read())

def load_public_key(path="public_key.pem"):
    """Loads an RSA public key from a file."""
    with open(path, "rb") as f:
        return RSA.import_key(f.read())

def sign_message(message: bytes, private_key) -> str:
    """Signs a message with a private key and returns the signature as a base64 string."""
    h = SHA256.new(message)
    signature = pkcs1_15.new(private_key).sign(h)
    return base64.b64encode(signature).decode('utf-8')

def verify_signature(message: bytes, signature: str, public_key) -> bool:
    """Verifies a signature with a public key."""
    h = SHA256.new(message)
    signature_bytes = base64.b64decode(signature)
    try:
        pkcs1_15.new(public_key).verify(h, signature_bytes)
        return True
    except (ValueError, TypeError):
        return False
