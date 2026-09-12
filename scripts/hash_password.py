import getpass
from argon2 import PasswordHasher


def main() -> None:
    password = getpass.getpass("Team password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    if len(password) < 8:
        raise SystemExit("Use at least 8 characters")
    print(PasswordHasher().hash(password))


if __name__ == "__main__":
    main()
