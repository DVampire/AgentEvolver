import os
import hvac
from dotenv import load_dotenv

load_dotenv(verbose=True)


class VaultClient:
    """Secret accessor that prefers Vault when available and falls back to env.

    If Vault (VAULT_ADDR / VAULT_TOKEN / SECRET_ENGINE_PATH) is configured and
    reachable, secrets are read from the secret engine. Otherwise the client
    operates in env-only mode and reads values straight from ``os.environ``,
    which ``load_dotenv`` has already populated from ``.env``.
    """

    def __init__(self, url: str = None, token: str = None, secret_engine_path: str = None):
        url = url or os.environ.get("VAULT_ADDR")
        token = token or os.environ.get("VAULT_TOKEN")
        self._path = secret_engine_path or os.environ.get("SECRET_ENGINE_PATH")
        self._client: hvac.Client | None = None
        self._cache: dict | None = None

        if url and token and self._path:
            try:
                client = hvac.Client(url=url, token=token)
                if client.is_authenticated():
                    self._client = client
            except Exception:
                # Vault unreachable / misconfigured -> fall back to env.
                self._client = None

    @property
    def enabled(self) -> bool:
        """Whether secrets are being served from Vault."""
        return self._client is not None

    def _read(self) -> dict:
        """Fetch secrets from Vault, using cache if already loaded."""
        if self._cache is None:
            if not self.enabled:
                self._cache = {}
            else:
                result = self._client.read(self._path)
                if result is None:
                    raise KeyError(f"Vault path not found: {self._path}")
                self._cache = result["data"]
        return self._cache

    def register(self, *keys: str) -> None:
        """Read specified keys from Vault and register them as environment variables.

        In env-only mode this is a no-op since the keys already come from ``.env``.
        """
        if not self.enabled:
            return
        data = self._read()
        for key in keys:
            if key not in data:
                raise KeyError(f"Key '{key}' not found in Vault path '{self._path}'")
            os.environ[key] = data[key].strip()

    def register_all(self) -> None:
        """Read all keys from Vault and register them as environment variables.

        In env-only mode this is a no-op since the keys already come from ``.env``.
        """
        if not self.enabled:
            return
        for key, value in self._read().items():
            os.environ[key] = value.strip()

    def get(self, key: str) -> str:
        """Retrieve a single value by key.

        Reads from Vault when enabled, otherwise from the environment (``.env``).
        Falls back to the environment when a key is absent from Vault.
        """
        if self.enabled:
            values = self._read()
            if key in values:
                return values[key].strip()
        return os.environ.get(key, "").strip()


hvac_client = VaultClient()
hvac_client.register_all()
