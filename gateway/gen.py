from cryptography.fernet import Fernet
import secrets

print("=== Gateway Configuration ===\n")
print(f"FERNET_KEY={Fernet.generate_key().decode()}")
print(f"SESSION_SECRET={secrets.token_urlsafe(32)}")
print(f"DASHBOARD_PASSWORD=your-strong-password-here")
print("\nCopy these to your .env file or export them before starting.")

