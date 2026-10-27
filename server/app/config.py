from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MHM_", env_file=".env", extra="ignore", hide_input_in_errors=True)
    database_url: str = "sqlite:///./data/monitor.db"
    admin_username: str = Field(default="admin", min_length=1, max_length=64)
    admin_password: str = Field(default="", repr=False)
    origin: str = "http://127.0.0.1:8000"
    cookie_secure: bool = False
    session_hours: int = Field(default=8, ge=1, le=24)

    retention_enabled: bool = True
    scheduler_enabled: bool = True
    probe_allowed_cidrs: list[str] = Field(default_factory=list, max_length=32)
    notification_allowed_cidrs: list[str] = Field(default_factory=list, max_length=32)
    notification_secret_dir: str = "data/secrets"

    @model_validator(mode="after")
    def validate_config(self):
        import ipaddress
        try:
            for cidr in self.probe_allowed_cidrs + self.notification_allowed_cidrs:
                ipaddress.ip_network(cidr, strict=True)
        except ValueError:
            raise ValueError("Outbound CIDR configuration is invalid") from None
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("A file-backed SQLite database is required")
        if self.database_url.endswith(":memory:"):
            raise ValueError("Use a file-backed database for WAL")
        if len(self.admin_password) < 12:
            raise ValueError("MHM_ADMIN_PASSWORD must contain at least 12 characters")
        from urllib.parse import urlsplit
        parsed = urlsplit(self.origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("MHM_ORIGIN must be an exact http(s) origin without trailing slash")
        if self.cookie_secure and parsed.scheme != "https":
            raise ValueError("Secure cookies require an HTTPS origin")
        return self
