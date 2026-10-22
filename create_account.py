"""Provision a HomeNest account from a trusted server terminal."""

import argparse
import getpass
import secrets
import sys

from auth_store import AccountStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", required=True, choices=("User", "Manager", "Admin"))
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--generate-password",
        action="store_true",
        help="Generate a random password and print it once after account creation.",
    )
    args = parser.parse_args()

    if args.generate_password:
        password = secrets.token_urlsafe(24)
    else:
        password = getpass.getpass("Password (minimum 12 characters): ")
        confirmation = getpass.getpass("Confirm password: ")
        if password != confirmation:
            parser.error("Passwords do not match.")

    try:
        account = AccountStore().create_account(
            args.email, args.name, password, args.role,
        )
    except ValueError as error:
        parser.error(str(error))

    print(f"Created {account['role']} account: {account['email']}")
    if args.generate_password:
        print(f"Password (shown once; save it securely): {password}")
    else:
        print("Password stored as a salted PBKDF2 hash.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
