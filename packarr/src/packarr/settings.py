from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urlsplit


class Held(RuntimeError):
    """A candidate or operation needs review. Messages must never contain secrets."""


class Unavailable(Held):
    """A failed read can be retried without replaying a mutation."""


def origin(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise Held("invalid_service_url")
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError as exc:
        raise Held("invalid_service_port") from exc
    return f"{parts.scheme}://{parts.hostname.lower()}:{port}"


def absolute_dir(value: str) -> str:
    if not value.startswith("/") or "\\" in value or "\x00" in value or ".." in value.split("/"):
        raise Held("download_directory_must_be_absolute")
    if str(PurePosixPath(value)) == "/":
        raise Held("download_directory_must_not_be_root")
    return str(PurePosixPath(value))


@dataclass(frozen=True)
class Target:
    series_id: int
    season: int
    indexer_id: int
    topic_url: str

    def __post_init__(self):
        if any(type(v) is not int or v <= 0 for v in (self.series_id, self.season, self.indexer_id)):
            raise Held("invalid_target_ids")
        origin(self.topic_url)


@dataclass
class Settings:
    sonarr_url: str = "http://sonarr:8989"
    sonarr_api_key: str = field(default="", repr=False)
    transmission_url: str = "http://transmission:9091/transmission/rpc"
    transmission_username: str = field(default="", repr=False)
    transmission_password: str = field(default="", repr=False)
    transmission_download_dir: str = ""
    sonarr_download_dir: str = ""
    dry_run: bool = True
    poll_interval: int = 900
    min_seeders: int = 1
    max_pack_bytes: int = 64 * 1024**3
    state_dir: str = "/data/packarr"
    targets: list[Target] = field(default_factory=list)

    def __post_init__(self):
        origin(self.sonarr_url)
        origin(self.transmission_url)
        if type(self.dry_run) is not bool or type(self.poll_interval) is not int or self.poll_interval < 300:
            raise Held("invalid_poll_interval_or_dry_run")
        if type(self.min_seeders) is not int or self.min_seeders < 1:
            raise Held("invalid_min_seeders")
        if type(self.max_pack_bytes) is not int or self.max_pack_bytes < 1:
            raise Held("invalid_pack_size_limit")
        if self.targets and not self.sonarr_api_key:
            raise Held("sonarr_api_key_required")
        if not self.dry_run:
            absolute_dir(self.transmission_download_dir)
            absolute_dir(self.sonarr_download_dir)

    @classmethod
    def load(cls, data: dict):
        data = dict(data)
        data["targets"] = [Target(**item) for item in data.get("targets", [])]
        try:
            return cls(**data)
        except TypeError as exc:
            raise Held("invalid_configuration_fields") from exc
