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
            # W6-T4: "exit", or "signal N" for a child a signal killed. The exit code
            # cannot carry it: 143 is what a shell reports for a child killed by
            # SIGTERM and also what a child that returned 143 itself reports, and only
            # launch._child still has the signal number to tell them apart.
            "terminal": Kind.ENUM,
            "duration_ms": Kind.SIZE,
            "surfaces_received": Kind.SIZE,
            # W8-T1, the git-history backfill. Two commits of a history that reached no
            # external.outcome, counted by the reason: GitHub returned no check run for
            # the commit at all, and a check run that has not finished or whose
            # conclusion is a word neither the pass nor the fail table carries. They are
            # here rather than folded into surfaces_received because a skip is not a
            # record received, and because a verification nobody could read must not
            # become a passing one by being invisible. Both are absent, not 0, on a run
            # that fetched no check runs.
            "no_check_runs": Kind.SIZE,
            "check_run_incomplete": Kind.SIZE,
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
            # W8-T1: which harness stated this outcome, as external.correlation has
            # carried since design 6.3. A backfill writes two kinds of outcome about one
            # commit from two different places (`github:check-runs` and
            # `git:line-overlap`), and without this field a reader holding both cannot
            # tell what produced either. A short symbolic word, never an address: the
            # remote URL it was derived from is read into the process and dropped.
            "external_system": Kind.ENUM,
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
        # W5-T2, the shadow advisory. Everything a `telltale advise` page states about
        # one candidate, and nothing it computed: the six A-block counts read off a git
        # diff, the two shas they were read between, the series they were read against,
        # and per target the label of the stored run that says how strongly the forecast
        # may be read. No free text, no prompt, no path: the report is printed and the
        # payload is what a later reader can check it against.
        #
        # `label`, `readiness` and `forecast_run_ids` are keyed BY TARGET rather than
        # being lists parallel to `target`. A dropped entry in a parallel list would
        # silently move a label onto a different target, which is the cardinality
        # defect AGENTS.md names; a key cannot slide.
        "policy.advisory": {
            "advisory_id": Kind.ID,
            "action": Kind.ENUM,
            "policy_version": Kind.ENUM,
            "base_sha": Kind.ID,
            "head_sha": Kind.ID,
            "series_id": Kind.ID,
            "target": Kind.ENUM,
            "forecast_run_ids": Kind.ID,
            "label": Kind.ENUM,
            "readiness": Kind.ENUM,
            # The six ABLATION_A columns as {name: count}. SIZE, so a string that ever
            # reached here is dropped rather than stored in a field a reader sums, and
            # None stays None: a binary file leaves lines_added unknown and never 0.
            "features": Kind.SIZE,
        },
        "policy.intervention": {
            "advisory_id": Kind.ID,
            "action": Kind.ENUM,
            "policy_version": Kind.ENUM,
            "external_system": Kind.ENUM,
        },
    }
