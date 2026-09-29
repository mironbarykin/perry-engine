"""Compatibility exports for short- and long-term memory interfaces."""

from app.memory_vault import (
    append_transcript,
    run_memory_index_worker,
    run_memory_sync_scanner,
    scan_all_user_vaults,
    scan_user_vault,
    vault_path,
)
from app.storage import append_turn, get_core_profile, get_working_memory

__all__ = [
    "append_transcript",
    "append_turn",
    "get_core_profile",
    "get_working_memory",
    "run_memory_sync_scanner",
    "run_memory_index_worker",
    "scan_all_user_vaults",
    "scan_user_vault",
    "vault_path",
]
