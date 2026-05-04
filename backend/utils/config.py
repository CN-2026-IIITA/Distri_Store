"""
DistriStore — Configuration Parser
Loads and validates config.yaml, providing typed access to all settings.
"""

import os
import yaml
import secrets
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class NodeConfig:
    """Node identity settings."""
    node_id: str = "auto"
    name: str = "node-default"


@dataclass
class NetworkConfig:
    """Network ports and discovery settings."""
    discovery_port: int = 50000
    tcp_port: int = 50001
    broadcast_address: str = "255.255.255.255"
    discovery_interval: int = 5
    peer_timeout: int = 15
    swarm_key: str = "default_distristore_secret"


@dataclass
class StorageConfig:
    """Chunk storage settings."""
    chunk_dir: str = ".storage"
    chunk_size: int = 262144  # 256 KB
    max_storage_mb: int = 1024


@dataclass
class ReplicationConfig:
    """Replication strategy settings.

    mode = "kcopy"   → store full copy of each chunk on `factor` peers (storage = factor x).
    mode = "erasure" → split each chunk into `erasure_n` Reed-Solomon shards
                       (any `erasure_k` reconstruct). Storage = erasure_n / erasure_k x
                       (default 9/6 = 1.5x). Survives loss of (erasure_n - erasure_k) shards.
    """
    factor: int = 3
    mode: str = "kcopy"
    erasure_k: int = 6
    erasure_n: int = 9


@dataclass
class ApiConfig:
    """FastAPI server settings."""
    host: str = "0.0.0.0"
    port: int = 8000


@dataclass
class LoggingConfig:
    """Logging settings."""
    level: str = "DEBUG"
    file: str = "distristore.log"


@dataclass
class AppConfig:
    """Top-level application configuration."""
    node: NodeConfig = field(default_factory=NodeConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    replication: ReplicationConfig = field(default_factory=ReplicationConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _generate_node_id() -> str:
    """Generate a random 20-byte (40 hex chars) node ID."""
    return secrets.token_hex(20)


def load_config(config_path: str = None) -> AppConfig:
    """
    Load configuration from a YAML file.

    If config_path is None, searches for config.yaml in:
    1. Current working directory
    2. Project root (two levels up from this file)

    Returns:
        AppConfig with all settings populated.
    """
    if config_path is None:
        # Try common locations
        candidates = [
            Path.cwd() / "config.yaml",
            Path(__file__).resolve().parent.parent.parent / "config.yaml",
        ]
        for candidate in candidates:
            if candidate.exists():
                config_path = str(candidate)
                break

    if config_path is None or not Path(config_path).exists():
        # Return defaults if no config file found
        cfg = AppConfig()
        if cfg.node.node_id == "auto":
            cfg.node.node_id = _generate_node_id()
        return cfg

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f) or {}

    # Build config from raw YAML dict
    node_raw = raw.get("node", {})
    network_raw = raw.get("network", {})
    storage_raw = raw.get("storage", {})
    replication_raw = raw.get("replication", {})
    api_raw = raw.get("api", {})
    logging_raw = raw.get("logging", {})

    cfg = AppConfig(
        node=NodeConfig(**{k: v for k, v in node_raw.items() if k in NodeConfig.__dataclass_fields__}),
        network=NetworkConfig(**{k: v for k, v in network_raw.items() if k in NetworkConfig.__dataclass_fields__}),
        storage=StorageConfig(**{k: v for k, v in storage_raw.items() if k in StorageConfig.__dataclass_fields__}),
        replication=ReplicationConfig(**{k: v for k, v in replication_raw.items() if k in ReplicationConfig.__dataclass_fields__}),
        api=ApiConfig(**{k: v for k, v in api_raw.items() if k in ApiConfig.__dataclass_fields__}),
        logging=LoggingConfig(**{k: v for k, v in logging_raw.items() if k in LoggingConfig.__dataclass_fields__}),
    )

    # Auto-generate Node ID if requested
    if cfg.node.node_id == "auto":
        cfg.node.node_id = _generate_node_id()

    return cfg


# Singleton config — load once, use everywhere
_config: AppConfig | None = None


def get_config(config_path: str = None) -> AppConfig:
    """Get the singleton AppConfig instance."""
    global _config
    if _config is None:
        _config = load_config(config_path)
    return _config
