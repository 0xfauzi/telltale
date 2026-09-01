# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions here are research gates, not feature milestones. Section 21 of
[`docs/spec/telltale-architecture.md`](docs/spec/telltale-architecture.md) states what each
one has to demonstrate before it is called done, and none of them is assumed to pass.

## [Unreleased]

Nothing has been released. The version in `pyproject.toml` is `0.0.1` and the capture path
does not exist yet. What is in the tree today:

### Added

- The `telltale` package, Python 3.12, src layout, with an empty runtime dependency list.
  The collector is standard library only, and the `forecast-isolation` pre-commit hook
  allows the model stack to be imported in exactly one module.
- `telltale --version`, which prints `0.0.1`.
- `telltale doctor` as a stub that prints `doctor: not implemented` and exits 2, because a
  self-check that has not been written must not report success. A caller would read exit 0
  as "every surface round-trips".
- The gate set: pytest with an integration-only test tree, mypy strict, ruff check and
  format, deptry, codespell, vulture, gitleaks, and a pre-commit configuration that runs
  them. CI runs the same gates on pull requests and on `main`.
- Four repository-specific pre-commit hooks that make an invariant unbreakable rather than
  documented: `tests-live-under-integration`, `forecast-isolation`,
  `derived-writes-only-in-store` and `no-em-dash`.
- The optional extras `telltale[forecast]` (numpy, torch, timesfm 3.0.0) and
  `telltale[export]` (pyarrow). Neither is installed by a plain `uv sync`.
- The specification, the verified provider digest, the design and the orchestration
  protocol, under `docs/spec/` and `docs/design/`.
- The shared capture receiver used by the wave 0 fixture experiments, at
  `experiments/common/capture_receiver.py`.
- Brand and public-ready documents: the logo as SVG in light and dark, a wordmark, the
  rasterized mark, a README with hero, badges, quickstart, privacy table, claim-class table
  and architecture diagram, a documentation index, `LICENSE`, `CONTRIBUTING.md`,
  `SECURITY.md`, this file, `CITATION.cff`, issue templates and a pull request template.

### Not yet built

Everything the README's quickstart marks with a wave. The receiver, the sanitizer, the
store, the providers, the observers, the launcher, the reducers, the series compiler and
the forecasting laboratory all arrive in later waves, and each one lands with the task
report that measured it under [`docs/log/`](docs/log/).

[Unreleased]: https://github.com/0xfauzi/telltale/commits/main
