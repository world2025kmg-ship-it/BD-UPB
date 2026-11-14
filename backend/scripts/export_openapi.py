"""Exporte le contrat OpenAPI vers docs/openapi.json (à partager avec les intégrateurs).

Usage (depuis backend/) :  python -m scripts.export_openapi
"""
import json
from pathlib import Path

from app.main import create_app

TARGET = Path(__file__).resolve().parent.parent.parent / "docs" / "openapi.json"


def main() -> None:
    spec = create_app().openapi()
    TARGET.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Contrat écrit dans {TARGET}")


if __name__ == "__main__":
    main()
