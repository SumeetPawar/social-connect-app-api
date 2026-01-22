from py_vapid import Vapid01
from cryptography.hazmat.primitives.serialization import Encoding, PrivateFormat, PublicFormat, NoEncryption

if __name__ == "__main__":
    vapid = Vapid01()
    vapid.generate_keys()
    private_pem = vapid.private_key.private_bytes(
        encoding=Encoding.PEM,
        format=PrivateFormat.PKCS8,
        encryption_algorithm=NoEncryption()
    ).decode()
    public_pem = vapid.public_key.public_bytes(
        encoding=Encoding.PEM,
        format=PublicFormat.SubjectPublicKeyInfo
    ).decode()
    print("VAPID_PRIVATE_KEY =", private_pem)
    print("VAPID_PUBLIC_KEY =", public_pem)