"""Administrative commands: ``python -m nightloom_api.cli <command>``.

There is no public registration, so the first admin is created here::

    python -m nightloom_api.cli create-admin --email admin@example.org --display-name "Admin"

The password is read from an interactive prompt (asked twice) or, with
``--password-stdin``, from the first line of standard input. It is never
accepted as a command-line argument, where it would end up in shell history
and process listings.
"""

import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from nightloom_api.auth import service
from nightloom_api.auth.models import Role
from nightloom_api.auth.schemas import UserCreate
from nightloom_api.config import Settings, get_settings
from nightloom_api.infra import db

EXIT_OK = 0
EXIT_ERROR = 1


class CommandError(Exception):
    """A failure to report to the user (no traceback)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m nightloom_api.cli", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    create_admin = commands.add_parser(
        "create-admin",
        help="create an admin user (fails if the email already exists)",
        description="Create an admin user. Fails if a user with the email already exists.",
    )
    create_admin.add_argument("--email", required=True)
    create_admin.add_argument("--display-name", required=True)
    create_admin.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the password from the first line of standard input instead of prompting",
    )
    return parser


def read_password(
    *,
    from_stdin: bool,
    stdin: TextIO,
    prompt: Callable[[str], str] = getpass.getpass,
) -> str:
    """Read the new password from stdin or from two matching prompts."""
    if from_stdin:
        password = stdin.readline().rstrip("\r\n")
        if not password:
            raise CommandError("No password on standard input.")
        return password
    password = prompt("Password: ")
    if prompt("Repeat password: ") != password:
        raise CommandError("Passwords do not match.")
    return password


async def create_admin(settings: Settings, *, email: str, display_name: str, password: str) -> str:
    """Create the admin and return its normalized email."""
    try:
        # Same validation as POST /users.
        body = UserCreate(email=email, display_name=display_name, role=Role.ADMIN, password="")
    except ValueError as exc:
        raise CommandError("Invalid email address or display name.") from exc

    engine = db.create_engine(settings)
    try:
        async with db.create_sessionmaker(engine)() as session:
            user = await service.create_user(
                session,
                email=body.email,
                display_name=body.display_name,
                role=Role.ADMIN,
                password=password,
            )
    except (service.EmailTakenError, service.PasswordPolicyError) as exc:
        raise CommandError(str(exc)) from exc
    finally:
        await engine.dispose()
    return user.email


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    settings: Settings | None = None,
    prompt: Callable[[str], str] = getpass.getpass,
) -> int:
    """Run a command; returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        password = read_password(
            from_stdin=args.password_stdin, stdin=stdin or sys.stdin, prompt=prompt
        )
        email = asyncio.run(
            create_admin(
                settings or get_settings(),
                email=args.email,
                display_name=args.display_name,
                password=password,
            )
        )
    except CommandError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    print(f"Created admin {email}.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
