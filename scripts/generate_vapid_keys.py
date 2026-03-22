"""
Generate a VAPID key pair in raw base64url format for Web Push.
Output is ready to paste directly into .env and .env.local.
"""
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.backends import default_backend
import base64

if __name__ == "__main__":
    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    public_key = private_key.public_key()

    pub = public_key.public_numbers()
    raw_pub = b'\x04' + pub.x.to_bytes(32, 'big') + pub.y.to_bytes(32, 'big')
    pub_b64 = base64.urlsafe_b64encode(raw_pub).rstrip(b'=').decode()

    raw_priv = private_key.private_numbers().private_value.to_bytes(32, 'big')
    priv_b64 = base64.urlsafe_b64encode(raw_priv).rstrip(b'=').decode()

    print()
    print("# ── Backend (.env) ──────────────────────────────────────")
    print(f"VAPID_PRIVATE_KEY={priv_b64}")
    print(f"VAPID_PUBLIC_KEY={pub_b64}")
    print()
    print("# ── Frontend (.env.local) ───────────────────────────────")
    print(f"NEXT_PUBLIC_VAPID_PUBLIC_KEY={pub_b64}")
    print()
    print(f"Public key length (raw bytes): {len(raw_pub)}  ← must be 65")
    print(f"First byte: {hex(raw_pub[0])}  ← must be 0x4")