"""Create local configuration without embedding or printing credentials."""

import argparse
import getpass
import secrets
from pathlib import Path
from urllib.parse import quote

import orjson
from dotenv import dotenv_values

from app.config import Settings


def build_configuration(
    url: str, prefix: str, key: str, template: dict[str, str]
) -> dict[str, str]:
    if not key.strip() or any("\n" in x or "\r" in x for x in [url, prefix, key]):
        raise ValueError("Provide a nonempty API key and single-line settings")
    result = dict(template)
    result.update(
        {
            "KEITARO_BASE_URL": url,
            "CAMPAIGN_PREFIX": prefix,
            "KEITARO_API_KEY": key.strip(),
            "APP_TOKEN": secrets.token_urlsafe(32),
            "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        }
    )
    user = quote(result["POSTGRES_USER"], safe="")
    database = quote(result["POSTGRES_DB"], safe="")
    dsn = f"postgresql+asyncpg://{user}:{result['POSTGRES_PASSWORD']}@127.0.0.1:{result['POSTGRES_HOST_PORT']}/{database}"
    result["DATABASE_URL"], result["TEST_DATABASE_URL"] = dsn, dsn + "_test"
    Settings.model_validate({name.lower(): value for name, value in result.items()})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--prefix", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    destination = root / ".env"
    if destination.exists():
        raise SystemExit(
            ".env already exists; edit it explicitly instead of overwriting credentials."
        )
    template = {name: value or "" for name, value in dotenv_values(root / ".env.example").items()}
    result = build_configuration(
        args.url, args.prefix, getpass.getpass("Keitaro API key: "), template
    )
    with destination.open("x", encoding="utf-8") as output:
        output.write(
            "\n".join(f"{name}={orjson.dumps(value).decode()}" for name, value in result.items())
            + "\n"
        )
    print(
        "Created .env. APP_TOKEN and the database password are stored there; no credentials were printed."
    )


if __name__ == "__main__":
    main()
