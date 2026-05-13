"""
Run this script once to generate the bcrypt hash for your app password.
Then set APP_PASSWORD_HASH in your .env file (or Render environment variables).

Usage:
    python generate_password_hash.py
"""
import getpass
import bcrypt


def main():
    password = getpass.getpass("Enter the password you want to use: ")
    confirm = getpass.getpass("Confirm password: ")

    if password != confirm:
        print("Passwords do not match. Exiting.")
        return

    hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    print("\nYour APP_PASSWORD_HASH:")
    print(hashed)
    print("\nAdd this to your .env file or Render environment variables as APP_PASSWORD_HASH")


if __name__ == "__main__":
    main()
