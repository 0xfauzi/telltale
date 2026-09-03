"""The half of the one allowlist table that Telltale writes itself.

Split out of allowlist.py, which was at the 800-line ratchet. The cut follows the
WRITER rather than the mechanism: every type here is a payload this system builds from
a process it launched, a repository it observed or a statement an orchestrator made, so
it changes when launch.py, repo.py or `telltale outcome` changes. The provider tables
change when a provider does, which is a different clock and now a different file.

Not a second table: `allowlist.py` calls `telltale_tables()` and merges the result into
the one ALLOWLIST at import, and nothing else imports this module. Same shape as
allowlist_codex.py, and for the same reason.
"""

from __future__ import annotations

from telltale.allowlist import Kind


def telltale_tables() -> dict[str, dict[str, Kind]]:
    """The `telltale.*`, `external.*` and `policy.*` types of design 6.3."""
    return {
        "telltale.capture_started": {
            "provider": Kind.ENUM,
            "argv_shape": Kind.ENUM,  # executable and flag names only, never values
            "content_level": Kind.SIZE,
            "surfaces_configured": Kind.ENUM,
            "provider_session_id_requested": Kind.ID,
            "task_id": Kind.ID,
            "attempt": Kind.SIZE,
            "experiment": Kind.ID,
            "worktree_id": Kind.ID,
            # W1-T1, on E01 finding 6: the names the launcher took out of the child's
            # environment. Names only, never values, which are in NEVER_PERSIST.
            "env_removed": Kind.ENUM,
            # W2-T2, the backfill importer. There was no launch, so argv_shape is the
            # word "backfill" and these four say which file this capture was read out
            # of. The path is HASHED: a transcript directory name encodes the project
            # directory it came from, and design 12.1 says the path is not the identity.
            "source_kind": Kind.ENUM,
            "source_path_hash": Kind.ID,
            "source_bytes": Kind.SIZE,
            "source_lines": Kind.SIZE,
        },
        "telltale.capture_ended": {
            "exit_code": Kind.SIZE,
            "duration_ms": Kind.SIZE,
            "surfaces_received": Kind.SIZE,
            # W0-T3 measured that worktree_id alone is the same for every main worktree
            # of every repository, so a capture is keyed by (repo_id, worktree_id) and
            # both ends of it carry the pair: repo_id is a column, this is the other.
            "worktree_id": Kind.ID,
        },
        "telltale.environment": {
            "provider": Kind.ENUM,
            "runtime_version": Kind.ENUM,
            "model": Kind.ENUM,
            "effort": Kind.ENUM,
            "tool_set_hash": Kind.ID,
            "mcp_names_hash": Kind.ID,
            "instruction_hashes": Kind.PATH,  # {path: {sha256, bytes}}: keys are paths
            "settings_hash": Kind.ID,
            "sandbox_posture": Kind.ENUM,
            "capture_modes": Kind.ENUM,
            "content_level": Kind.SIZE,
        },
        "telltale.repo.identity": {
            "repo_id": Kind.ID,
            "worktree_id": Kind.ID,
            "root_hash": Kind.ID,
            "head": Kind.ID,
            "branch": Kind.ENUM,
            "base_sha": Kind.ID,
            "remote_fingerprint": Kind.ID,
            "dirty_tree_hash": Kind.ID,
        },
        "telltale.repo.snapshot": {
            "trigger": Kind.ENUM,
            "head": Kind.ID,
            "dirty_tree_hash": Kind.ID,
            "diff_hash": Kind.ID,
            "files_changed": Kind.SIZE,
            "additions": Kind.SIZE,
            "deletions": Kind.SIZE,
            "renames": Kind.SIZE,
            "staged_files": Kind.SIZE,
            "unstaged_files": Kind.SIZE,
            "untracked_count": Kind.SIZE,
            "per_file": Kind.PATH,  # [{path, additions, deletions, patch_hash}]
            # True when per_file is a prefix rather than the whole list (launch.py
            # caps it so the 8 KB payload bound cannot drop the list whole).
            # files_changed still carries the true count.
            "per_file_truncated": Kind.SCALAR,
        },
        "telltale.repo.commit": {
            "sha": Kind.ID,
            "parents": Kind.ID,
            "tree": Kind.ID,
            "committed_ts": Kind.ENUM,
            "files_changed": Kind.SIZE,
            "additions": Kind.SIZE,
            "deletions": Kind.SIZE,
            "link_confidence": Kind.ENUM,
            # W3-T4: [{path, additions, deletions}], from the numstat repo_link.py
            # already reads. THREE keys, not the snapshot's four: repo.per_file measured
            # what a patch_hash per entry costs, and it puts this repository's largest
            # commit past the 8 KB bound. The two flags below carry the same meaning
            # they carry on a snapshot.
            "per_file": Kind.PATH,
            "per_file_truncated": Kind.SCALAR,
        },
        "external.correlation": {
            "external_system": Kind.ENUM,
            "external_run_id": Kind.ID,
            "component_id": Kind.ID,
            "task_id": Kind.ID,
            "attempt": Kind.SIZE,
            "provider_session_id": Kind.ID,
        },
        "external.outcome": {
            "kind": Kind.ENUM,
            "status": Kind.ENUM,
            "categories": Kind.ENUM,
            "timestamp": Kind.ENUM,
            "external_run_id": Kind.ID,
            "component_id": Kind.ID,
            "attempt": Kind.SIZE,
            # W5-T1: how long the outcome's own run took, in whole milliseconds. The
            # change clock's merge_verification_ms reads it off a
            # mechanical_verification outcome. SIZE, like every other duration_ms in
            # this table, so a string from a wire is dropped rather than stored in a
            # column later code sums. `telltale outcome --duration-ms` is `type=int`,
            # so a caller cannot state a fraction of a millisecond it did not measure.
            "duration_ms": Kind.SIZE,
        },
        "policy.intervention": {
            "advisory_id": Kind.ID,
            "action": Kind.ENUM,
            "policy_version": Kind.ENUM,
            "external_system": Kind.ENUM,
        },
    }
