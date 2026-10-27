"""Versioned, bounded wire contract. Times are UTC Unix seconds, sizes are bytes."""
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Bytes = Annotated[int, Field(ge=0, le=2**63 - 1)]
Percent = Annotated[float, Field(ge=0, le=100)]
Load = Annotated[float, Field(ge=0, le=1_000_000)]
Unit = Annotated[str, Field(pattern=r"^[A-Za-z0-9_:@.\-]{1,120}\.service$")]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True)


class HostCreate(Wire):
    name: str = Field(min_length=1, max_length=128)
    hostname: str = Field(min_length=1, max_length=255)
    cloud_provider: str = Field(default="", max_length=64)
    region: str = Field(default="", max_length=64)
    tags: list[Annotated[str, Field(min_length=1, max_length=64)]] = Field(default_factory=list, max_length=32)
    systemd_units: list[Unit] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.name.strip() or not self.hostname.strip():
            raise ValueError("Name and hostname cannot be blank")
        if len(set(self.tags)) != len(self.tags) or len(set(self.systemd_units)) != len(self.systemd_units):
            raise ValueError("Duplicate configuration targets")
        return self


class Metadata(Wire):
    hostname: str = Field(min_length=1, max_length=255)
    os: str = Field(min_length=1, max_length=128)
    arch: Literal["amd64", "arm64"]
    agent_version: str = Field(min_length=1, max_length=64)


class Heartbeat(Wire):
    host_id: int = Field(gt=0)
    sampled_at: int = Field(gt=0)
    metadata: Metadata


class Disk(Wire):
    mountpoint: str = Field(min_length=1, max_length=512)
    total_bytes: Bytes
    used_bytes: Bytes
    available_bytes: Bytes
    inode_percent: Percent | None

    @model_validator(mode="after")
    def ranges(self):
        if (self.total_bytes == 0 or self.used_bytes > self.total_bytes or self.available_bytes > self.total_bytes
                or not self.mountpoint.startswith('/') or '..' in self.mountpoint.split('/')):
            raise ValueError("Invalid disk")
        return self


class Network(Wire):
    interface: str = Field(pattern=r"^[A-Za-z0-9_.:@\-]{1,64}$")
    received_bytes: Bytes
    sent_bytes: Bytes
    receive_rate: Annotated[float, Field(ge=0, le=1e15)] | None
    send_rate: Annotated[float, Field(ge=0, le=1e15)] | None


class Service(Wire):
    unit: Unit
    state: Literal['active', 'inactive', 'failed', 'activating', 'deactivating', 'reloading', 'unknown', 'unavailable']


class Sample(Wire):
    sample_id: str = Field(min_length=36, max_length=36)
    sampled_at: int = Field(gt=0)
    metadata: Metadata
    cpu_percent: Percent
    load_1: Load
    load_5: Load
    load_15: Load
    memory_total_bytes: Bytes
    memory_available_bytes: Bytes
    swap_total_bytes: Bytes
    swap_used_bytes: Bytes
    uptime_seconds: int = Field(ge=0, le=2**40)
    disks: list[Disk] = Field(max_length=32)
    networks: list[Network] = Field(max_length=32)
    services: list[Service] = Field(max_length=32)

    @model_validator(mode="after")
    def validate_sample(self):
        parsed = UUID(self.sample_id)
        if str(parsed) != self.sample_id or parsed.version != 4:
            raise ValueError('sample_id must be a canonical UUID v4')
        if (self.memory_total_bytes == 0 or self.memory_available_bytes > self.memory_total_bytes
                or self.swap_used_bytes > self.swap_total_bytes):
            raise ValueError('Invalid memory')
        for values, key in [(self.disks, 'mountpoint'), (self.networks, 'interface'), (self.services, 'unit')]:
            names = [getattr(item, key) for item in values]
            if len(names) != len(set(names)):
                raise ValueError('Duplicate target')
        # No arbitrary shell strings or server-defined commands exist in this protocol.
        if not re.fullmatch(r'[A-Za-z0-9_.+\-]{1,64}', self.metadata.agent_version):
            raise ValueError('Invalid version')
        return self


class Batch(Wire):
    host_id: int = Field(gt=0)
    samples: list[Sample] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def unique(self):
        if len({s.sample_id for s in self.samples}) != len(self.samples):
            raise ValueError('Duplicate IDs within batch')
        if len({s.sampled_at for s in self.samples}) != len(self.samples):
            raise ValueError('Duplicate sample times within batch')
        return self
