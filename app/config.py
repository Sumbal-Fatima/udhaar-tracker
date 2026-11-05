"""Settings are read at call time so tests and deployments can change env vars freely."""
import os


def api_key() -> str:
    return os.getenv("VULTR_INFERENCE_API_KEY", "").strip()


def base_url() -> str:
    return os.getenv("VULTR_INFERENCE_BASE_URL", "https://api.vultrinference.com/v1").rstrip("/")


def model() -> str:
    return os.getenv("VULTR_MODEL", "llama-3.3-70b-instruct-fp8").strip()


def database_path() -> str:
    return os.getenv("DATABASE_PATH", "./data/udhaar.db")


def auto_seed() -> bool:
    return os.getenv("AUTO_SEED", "true").lower() in ("1", "true", "yes")
