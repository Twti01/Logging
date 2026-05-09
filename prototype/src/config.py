"""Laedt die zentrale YAML-Konfiguration."""
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent


def load_config(path: str | None = None) -> dict:
    """Liest die Konfiguration relativ zum Projektroot."""
    cfg_path = Path(path) if path else ROOT / "configs" / "config.yaml"
    with open(cfg_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


if __name__ == "__main__":
    cfg = load_config()
    print("OK - Konfiguration geladen:")
    for k, v in cfg.items():
        print(f"  {k}: {v}")
