"""E15: four pre-registered prediction tasks on what Telltale records.

See docs/experiments/E15.md.

Everything is out of fold: five folds grouped by capture (sha256 of the capture id
modulo
5), models fit on four folds and scored on the fifth, metrics pooled over the five and
reported per fold beside the pooled number. Cohorts (claude/import, claude/launcher) are
never pooled. numpy only (the forecast extra); nothing here is imported by the
collector.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import sqlite3
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
DB = HERE.parent / "E13" / "out" / "home" / "telltale.db"
FOLDS = 5
PREFIX = 16
MIN_REQUESTS = 32
RIDGE = 1.0
L2 = 1.0
BARS = {
    "auc": 0.70,
    "brier_skill": 0.10,
    "mae_skill": 0.10,
    "spearman": 0.50,
    "logloss_reduction": 0.10,
}
KINDS = ("command", "file_read", "file_edit", "verification_run")
CATEGORIES = (
    "test",
    "typecheck",
    "lint",
    "format",
    "build",
    "shell",
    "git",
    "package_op",
    "process_mgmt",
    "unknown",
)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
TOOL_TYPES = ("model_request", "command", "file_edit", "file_read", "verification_run")

# Names for the shapes these functions pass to each other, so signatures fit one line.
Act = dict[str, Any]
Feats = list[float]
Row = tuple[Feats, int]
Held = tuple[Feats, int, str]
LRow = tuple[Feats, float]
Metrics = dict[str, Any]
Cols = dict[str, list[float]]
Counts = collections.Counter[str]
Arr = np.ndarray
Arrays = tuple[Arr, ...]
EventFolds = dict[int, list[Held]]
LevelFolds = dict[int, list[tuple[Feats, Feats]]]
SeqFolds = dict[int, list[list[str]]]


def fold_of(capture_id: str) -> int:
    return int(hashlib.sha256(capture_id.encode()).hexdigest(), 16) % FOLDS


def cohort_of(capture_id: str) -> str:
    return "claude/import" if capture_id.startswith("imp_") else "claude/launcher"


def load(db: Path) -> dict[str, list[Act]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    caps = [
        r[0]
        for r in con.execute("select capture_id from captures where provider='claude'")
    ]
    # five placeholders for the five TOOL_TYPES; the values are bound as parameters
    assert len(TOOL_TYPES) == 5
    rows = con.execute(
        "select capture_id, activity_type, started_at, fields from activities"
        " where activity_type in (?,?,?,?,?)"
        " order by capture_id, started_at, activity_id",
        TOOL_TYPES,
    )
    by_cap: dict[str, list[Act]] = collections.defaultdict(list)
    wanted = set(caps)
    for cap, kind, ts, fields in rows:
        if cap in wanted:
            by_cap[cap].append({"kind": kind, "ts": ts, **json.loads(fields)})
    con.close()
    return dict(by_cap)


# -- metrics --
def auc(y: np.ndarray, p: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    order = np.argsort(np.concatenate([pos, neg]), kind="mergesort")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    # ties: average ranks
    values = np.concatenate([pos, neg])
    for v in np.unique(values):
        mask = values == v
        if mask.sum() > 1:
            ranks[mask] = ranks[mask].mean()
    return float(
        (ranks[: len(pos)].sum() - len(pos) * (len(pos) + 1) / 2)
        / (len(pos) * len(neg))
    )


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra, rb = _rank(a), _rank(b)
    ra, rb = ra - ra.mean(), rb - rb.mean()
    d = math.sqrt(float((ra**2).sum() * (rb**2).sum()))
    return float((ra * rb).sum() / d) if d else 0.0


def _rank(v: np.ndarray) -> np.ndarray:
    order = np.argsort(v, kind="mergesort")
    r = np.empty(len(v))
    r[order] = np.arange(len(v))
    for u in np.unique(v):
        m = v == u
        if m.sum() > 1:
            r[m] = r[m].mean()
    return r


# -- models --
class Scaler:
    def fit(self, x: np.ndarray) -> Scaler:
        self.mu = x.mean(axis=0)
        self.sd = x.std(axis=0)
        self.sd[self.sd == 0] = 1.0
        return self

    def apply(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mu) / self.sd


def logistic_fit(
    x: np.ndarray, y: np.ndarray, l2: float = L2, iters: int = 30
) -> np.ndarray:
    xb = np.hstack([np.ones((len(x), 1)), x])
    w = np.zeros(xb.shape[1])
    reg = np.full(xb.shape[1], l2)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-xb @ w))
        g = xb.T @ (p - y) + reg * w
        h = (xb * (p * (1 - p))[:, None]).T @ xb + np.diag(reg)
        step = np.linalg.solve(h, g)
        w -= step
        if float(np.abs(step).max()) < 1e-8:
            break
    return w


def logistic_predict(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    xb = np.hstack([np.ones((len(x), 1)), x])
    return 1 / (1 + np.exp(-xb @ w))


def ridge_fit(x: np.ndarray, y: np.ndarray, lam: float = RIDGE) -> np.ndarray:
    xb = np.hstack([np.ones((len(x), 1)), x])
    reg = np.eye(xb.shape[1]) * lam
    reg[0, 0] = 0
    return np.linalg.solve(xb.T @ xb + reg, xb.T @ y)


def ridge_predict(w: np.ndarray, x: np.ndarray) -> np.ndarray:
    return np.hstack([np.ones((len(x), 1)), x]) @ w


# -- task rows --
class _VState:
    """The counters rows_v carries between verification runs. after_run resets them,
    so a row's features cover only the stretch of session since the previous run.
    """

    def __init__(self) -> None:
        self.prev: int | None = None
        self.since_fail = 0
        self.edits = 0
        self.files: set[str] = set()
        self.cmds = 0
        self.reqs = 0
        self.index = 0

    def observe(self, kind: str, act: Act) -> None:
        if kind == "file_edit":
            self.edits += 1
            if act.get("path"):
                self.files.add(str(act["path"]))
        elif kind == "command":
            self.cmds += 1
        elif kind == "model_request":
            self.reqs += 1

    def after_run(self, failed: int) -> None:
        self.prev = 0 if failed else 1
        self.since_fail = 0 if failed else self.since_fail + 1
        self.edits = 0
        self.files = set()
        self.cmds = 0
        self.reqs = 0


def _v_feats(state: _VState, act: Act) -> Feats:
    cat = act.get("category")
    scope = act.get("scope")
    return [
        1.0 if state.prev == 0 else 0.0,
        1.0 if state.prev is None else 0.0,
        math.log1p(state.since_fail),
        math.log1p(state.edits),
        math.log1p(len(state.files)),
        math.log1p(state.cmds),
        math.log1p(state.reqs),
        math.log1p(state.index),
        *[1.0 if cat == c else 0.0 for c in ("test", "typecheck", "lint")],
        1.0 if scope == "targeted" else 0.0,
        1.0 if scope == "full" else 0.0,
    ]


def rows_v(_cap: str, acts: list[Act]) -> list[Row]:
    """One row per verification run with a known result, features from its past only."""
    out: list[Row] = []
    state = _VState()
    for a in acts:
        k = a["kind"]
        if k == "verification_run" and a.get("success") in (0, 1, True, False):
            state.index += 1
            feats = _v_feats(state, a)
            failed = 0 if a["success"] else 1
            out.append((feats, failed))
            state.after_run(failed)
        else:
            state.observe(k, a)
    return out


def rows_c(_cap: str, acts: list[Act]) -> list[Row]:
    out = []
    last5: collections.deque[int] = collections.deque(maxlen=5)
    prev_failed = 0.0
    edits = 0
    for a in acts:
        k = a["kind"]
        if k in ("command", "verification_run") and a.get("success") in (
            0,
            1,
            True,
            False,
        ):
            cat = a.get("category")
            feats = [
                prev_failed,
                float(sum(last5)),
                math.log1p(edits),
                1.0 if k == "verification_run" else 0.0,
                *[1.0 if cat == c else 0.0 for c in CATEGORIES],
            ]
            failed = 0 if a["success"] else 1
            out.append((feats, failed))
            last5.append(failed)
            prev_failed = float(failed)
            edits = 0
        elif k == "file_edit":
            edits += 1
    return out


def _l_head_feats(head: list[Act]) -> Feats:
    """Token counts over the first PREFIX requests of a session."""
    out_head = [float(a.get("output_tokens") or 0) for a in head]
    fresh_head = [float(a.get("input_tokens") or 0) for a in head]
    return [
        math.log1p(sum(out_head)),
        math.log1p(statistics.median(out_head)),
        math.log1p(sum(fresh_head)),
        math.log1p(statistics.median(fresh_head)),
        math.log1p(float(head[-1].get("cache_read_tokens") or 0)),
    ]


def _l_context_feats(acts: list[Act], cutoff: str) -> Feats:
    """Non-request activity, and failures, at or before the prefix cutoff."""
    mix = collections.Counter(
        a["kind"]
        for a in acts
        if a["kind"] != "model_request" and (a["ts"] or "") <= cutoff
    )
    fails = sum(
        1
        for a in acts
        if a["kind"] in ("command", "verification_run")
        and (a["ts"] or "") <= cutoff
        and a.get("success") in (0, False)
    )
    return [
        math.log1p(mix["file_edit"]),
        math.log1p(mix["file_read"]),
        math.log1p(mix["command"]),
        math.log1p(mix["verification_run"]),
        math.log1p(fails),
    ]


def row_l(_cap: str, acts: list[Act], models: list[str]) -> tuple[Feats, Feats] | None:
    reqs = [a for a in acts if a["kind"] == "model_request"]
    if len(reqs) < MIN_REQUESTS:
        return None
    head, tail = reqs[:PREFIX], reqs[PREFIX:]
    cutoff = head[-1]["ts"] or ""
    model = str(head[0].get("model"))
    feats = [
        *_l_head_feats(head),
        *_l_context_feats(acts, cutoff),
        *[1.0 if model == m else 0.0 for m in models],
    ]
    targets = [
        math.log1p(sum(float(a.get("output_tokens") or 0) for a in tail)),
        math.log1p(len(tail)),
    ]
    return feats, targets


def seq_p(acts: list[Act]) -> list[str]:
    return [a["kind"] for a in acts if a["kind"] in KINDS]


# -- evaluation --
def _label(clears: bool, every: bool) -> str:
    """The wording rule the three tasks share."""
    if clears and every:
        return "predictable at this n"
    if clears:
        return "unstable"
    return "not predictable from these inputs"


def _event_folds(data: dict[str, list[Row]]) -> EventFolds:
    folds: EventFolds = collections.defaultdict(list)
    for cap, rows in data.items():
        folds[fold_of(cap)].extend((f, y, cap) for f, y in rows)
    return folds


def _event_train(folds: EventFolds, k: int) -> list[Row]:
    return [(f, y) for j in range(FOLDS) if j != k for f, y, _ in folds[j]]


def _event_unscorable(train: list[Row], test: list[Held]) -> bool:
    return not test or sum(y for _, y, _ in test) < 5 or sum(y for _, y in train) < 5


def _event_fold(
    name: str, k: int, train: list[Row], test: list[Held]
) -> tuple[Metrics, Arrays]:
    """Fit on the four training folds, score the held-out one."""
    xtr = np.array([f for f, _ in train])
    ytr = np.array([y for _, y in train], dtype=float)
    xte = np.array([f for f, _, _ in test])
    yte = np.array([y for _, y, _ in test], dtype=float)
    sc = Scaler().fit(xtr)
    w = logistic_fit(sc.apply(xtr), ytr)
    p = logistic_predict(w, sc.apply(xte))
    base = np.full(len(yte), ytr.mean())
    # persistence: the first feature is "previous failed" for V and C alike;
    # with no previous result, the base rate
    persist = np.where(xte[:, 0] > 0.5, 1.0, 0.0)
    if name == "V":
        persist = np.where(xte[:, 1] > 0.5, ytr.mean(), persist)
    row = {
        "fold": k,
        "n": len(yte),
        "positives": int(yte.sum()),
        "auc": auc(yte, p),
        "brier": brier(yte, p),
        "brier_base": brier(yte, base),
        "brier_persist": brier(yte, persist),
        "brier_skill_vs_best_null": 1
        - brier(yte, p) / min(brier(yte, base), brier(yte, persist)),
    }
    return row, (yte, p, base, persist)


def _event_scored_folds(name: str, folds: EventFolds) -> tuple[list[Metrics], Cols]:
    cols: Cols = {"y": [], "p": [], "base": [], "persist": []}
    per_fold: list[Metrics] = []
    for k in range(FOLDS):
        train = _event_train(folds, k)
        test = folds[k]
        if _event_unscorable(train, test):
            per_fold.append(
                {
                    "fold": k,
                    "n": len(test),
                    "positives": sum(y for _, y, _ in test),
                    "skipped": "fewer than 5 positives",
                }
            )
            continue
        row, (yte, p, base, persist) = _event_fold(name, k, train, test)
        per_fold.append(row)
        cols["y"] += list(yte)
        cols["p"] += list(p)
        cols["base"] += list(base)
        cols["persist"] += list(persist)
    return per_fold, cols


def _event_summary(y: Arr, p: Arr, b: Arr, s: Arr, per_fold: list[Metrics]) -> Metrics:
    best_null = min(brier(y, b), brier(y, s))
    pooled = {
        "n": len(y),
        "positives": int(y.sum()),
        "base_rate": float(y.mean()),
        "auc": auc(y, p),
        "auc_persistence": auc(y, s),
        "brier": brier(y, p),
        "brier_base": brier(y, b),
        "brier_persist": brier(y, s),
        "brier_skill_vs_best_null": 1 - brier(y, p) / best_null,
    }
    scored = [f for f in per_fold if "skipped" not in f]
    clears = (pooled["auc"] or 0) >= BARS["auc"] and pooled[
        "brier_skill_vs_best_null"
    ] >= BARS["brier_skill"]
    every = (
        all(f["brier_skill_vs_best_null"] > 0 for f in scored) and len(scored) == FOLDS
    )
    return {"pooled": pooled, "folds": per_fold, "label": _label(clears, every)}


def evaluate_event(name: str, data: dict[str, list[Row]]) -> Metrics:
    """data: capture -> rows.

    Returns pooled and per-fold metrics for model and nulls.
    """
    per_fold, cols = _event_scored_folds(name, _event_folds(data))
    y, p, b, s = (np.array(cols[key]) for key in ("y", "p", "base", "persist"))
    if len(y) == 0:
        return {
            "n": 0,
            "label": "not assessable: no fold had 5 positives",
            "folds": per_fold,
        }
    return _event_summary(y, p, b, s, per_fold)


def _level_folds(data: dict[str, tuple[Feats, Feats]]) -> LevelFolds:
    folds: LevelFolds = collections.defaultdict(list)
    for cap, (f, t) in data.items():
        folds[fold_of(cap)].append((f, t))
    return folds


def _level_train(folds: LevelFolds, k: int, ti: int) -> list[LRow]:
    return [(f, t[ti]) for j in range(FOLDS) if j != k for f, t in folds[j]]


def _level_fold(k: int, train: list[LRow], test: list[LRow]) -> tuple[Metrics, Arrays]:
    """Ridge on the four training folds, scored on the held-out one."""
    xtr = np.array([f for f, _ in train])
    ytr = np.array([y for _, y in train])
    xte = np.array([f for f, _ in test])
    yte = np.array([y for _, y in test])
    sc = Scaler().fit(xtr)
    w = ridge_fit(sc.apply(xtr), ytr)
    p = ridge_predict(w, sc.apply(xte))
    null = np.full(len(yte), float(np.median(ytr)))
    mae, mae_null = float(np.abs(p - yte).mean()), float(np.abs(null - yte).mean())
    row = {
        "fold": k,
        "n": len(yte),
        "mae_log": mae,
        "mae_null_log": mae_null,
        "mae_skill": 1 - mae / mae_null,
        "spearman": spearman(p, yte),
    }
    return row, (yte, p, null)


def _level_scored_folds(folds: LevelFolds, ti: int) -> tuple[list[Metrics], Cols]:
    cols: Cols = {"y": [], "p": [], "null": []}
    per_fold: list[Metrics] = []
    for k in range(FOLDS):
        train = _level_train(folds, k, ti)
        test = [(f, t[ti]) for f, t in folds[k]]
        if len(test) < 5 or len(train) < 20:
            per_fold.append({"fold": k, "n": len(test), "skipped": "too few sessions"})
            continue
        row, (yte, p, null) = _level_fold(k, train, test)
        per_fold.append(row)
        cols["y"] += list(yte)
        cols["p"] += list(p)
        cols["null"] += list(null)
    return per_fold, cols


def _level_summary(y: Arr, p: Arr, nl: Arr, per_fold: list[Metrics]) -> Metrics:
    mae, mae_null = float(np.abs(p - y).mean()), float(np.abs(nl - y).mean())
    pooled = {
        "n": len(y),
        "mae_log": mae,
        "mae_null_log": mae_null,
        "mae_skill": 1 - mae / mae_null,
        "spearman": spearman(p, y),
        "median_abs_error_ratio": float(np.exp(np.median(np.abs(p - y)))),
    }
    scored = [f for f in per_fold if "skipped" not in f]
    clears = (
        pooled["mae_skill"] >= BARS["mae_skill"]
        and pooled["spearman"] >= BARS["spearman"]
    )
    every = all(f["mae_skill"] > 0 for f in scored) and len(scored) == FOLDS
    return {"pooled": pooled, "folds": per_fold, "label": _label(clears, every)}


def _level_target(folds: LevelFolds, ti: int) -> Metrics:
    per_fold, cols = _level_scored_folds(folds, ti)
    y, p, nl = (np.array(cols[key]) for key in ("y", "p", "null"))
    if len(y) == 0:
        return {"label": "not assessable", "folds": per_fold}
    return _level_summary(y, p, nl, per_fold)


def evaluate_level(data: dict[str, tuple[Feats, Feats]]) -> Metrics:
    folds = _level_folds(data)
    targets = ("log_remaining_output_tokens", "log_remaining_requests")
    return {tname: _level_target(folds, ti) for ti, tname in enumerate(targets)}


class _Markov:
    """Add-one smoothed next-kind counts, fitted on one split's training folds. lag2
    falls back to lag1 below 20 observations of the pair: the pre-registered rule.
    """

    def __init__(self, train: list[list[str]]) -> None:
        self.base: Counts = collections.Counter()
        self.lag1: collections.Counter[tuple[str, str]] = collections.Counter()
        self.lag2: collections.Counter[tuple[str, str, str]] = collections.Counter()
        for s in train:
            for i in range(1, len(s)):
                self.base[s[i]] += 1
                self.lag1[(s[i - 1], s[i])] += 1
                if i >= 2:
                    self.lag2[(s[i - 2], s[i - 1], s[i])] += 1
        self.nb = sum(self.base.values())

    def p_base(self, nxt: str) -> float:
        return (self.base[nxt] + 1) / (self.nb + len(KINDS))

    def p_lag1(self, prev: str, nxt: str) -> float:
        row = sum(self.lag1[(prev, x)] for x in KINDS)
        return (self.lag1[(prev, nxt)] + 1) / (row + len(KINDS))

    def p_lag2(self, p2: str, p1: str, nxt: str) -> float:
        row = sum(self.lag2[(p2, p1, x)] for x in KINDS)
        if row < 20:
            return self.p_lag1(p1, nxt)
        return (self.lag2[(p2, p1, nxt)] + 1) / (row + len(KINDS))


def _seq_step(m: _Markov, s: list[str], i: int, ll: Counts, acc: Counts) -> None:
    """Fold one transition of one test sequence into the log loss and hit counts."""
    for name, prob in (
        ("base", m.p_base(s[i])),
        ("lag1", m.p_lag1(s[i - 1], s[i])),
        ("lag2", m.p_lag2(s[i - 2], s[i - 1], s[i])),
    ):
        ll[name] += -math.log(prob)
    acc["base"] += 1 if s[i] == max(KINDS, key=m.p_base) else 0
    acc["lag1"] += 1 if s[i] == max(KINDS, key=lambda x: m.p_lag1(s[i - 1], x)) else 0
    acc["lag2"] += (
        1 if s[i] == max(KINDS, key=lambda x: m.p_lag2(s[i - 2], s[i - 1], x)) else 0
    )


def _seq_score(m: _Markov, test: list[list[str]]) -> tuple[int, Counts, Counts]:
    ll: Counts = collections.Counter()
    acc: Counts = collections.Counter()
    n = 0
    for s in test:
        for i in range(2, len(s)):
            n += 1
            _seq_step(m, s, i, ll, acc)
    return n, ll, acc


def _seq_folds(data: dict[str, list[str]]) -> SeqFolds:
    folds: SeqFolds = collections.defaultdict(list)
    for cap, seq in data.items():
        folds[fold_of(cap)].append(seq)
    return folds


def _seq_fold_row(k: int, n: int, ll: Counts, acc: Counts) -> Metrics:
    return {
        "fold": k,
        "n": n,
        "logloss_base": ll["base"] / n,
        "logloss_lag1": ll["lag1"] / n,
        "logloss_lag2": ll["lag2"] / n,
        "reduction_lag1": 1 - ll["lag1"] / ll["base"],
        "reduction_lag2": 1 - ll["lag2"] / ll["base"],
        "accuracy_base": acc["base"] / n,
        "accuracy_lag1": acc["lag1"] / n,
        "accuracy_lag2": acc["lag2"] / n,
    }


def _seq_scored_folds(folds: SeqFolds) -> tuple[list[Metrics], Counts]:
    per_fold: list[Metrics] = []
    totals: Counts = collections.Counter()
    for k in range(FOLDS):
        train = [s for j in range(FOLDS) if j != k for s in folds[j]]
        n, ll, acc = _seq_score(_Markov(train), folds[k])
        if n == 0:
            per_fold.append({"fold": k, "n": 0, "skipped": "no transitions"})
            continue
        per_fold.append(_seq_fold_row(k, n, ll, acc))
        for name in ("base", "lag1", "lag2"):
            totals[name] += ll[name]
            totals[name + "_acc"] += acc[name]
        totals["n"] += n
    return per_fold, totals


def evaluate_sequence(data: dict[str, list[str]]) -> Metrics:
    per_fold, totals = _seq_scored_folds(_seq_folds(data))
    if totals["n"] == 0:
        return {"label": "not assessable", "folds": per_fold}
    n = totals["n"]
    pooled = {
        "n": n,
        "logloss_base": totals["base"] / n,
        "logloss_lag1": totals["lag1"] / n,
        "logloss_lag2": totals["lag2"] / n,
        "reduction_lag1": 1 - totals["lag1"] / totals["base"],
        "reduction_lag2": 1 - totals["lag2"] / totals["base"],
        "accuracy_base": totals["base_acc"] / n,
        "accuracy_lag1": totals["lag1_acc"] / n,
        "accuracy_lag2": totals["lag2_acc"] / n,
    }
    scored = [f for f in per_fold if "skipped" not in f]
    clears = pooled["reduction_lag1"] >= BARS["logloss_reduction"]
    every = all(f["reduction_lag1"] > 0 for f in scored) and len(scored) == FOLDS
    return {"pooled": pooled, "folds": per_fold, "label": _label(clears, every)}


# -- assembly --
def _models_one_hot(caps: dict[str, list[Act]]) -> list[str]:
    model_counts = collections.Counter(
        str(next((a.get("model") for a in acts if a["kind"] == "model_request"), None))
        for acts in caps.values()
    )
    return [m for m, c in model_counts.most_common() if c >= 20 and m != "None"]


def _nonempty(rows: dict[str, Any]) -> dict[str, Any]:
    return {k: r for k, r in rows.items() if r}


def _cohort_rows(
    caps: dict[str, list[Act]], models: list[str]
) -> tuple[Metrics, Metrics, Metrics, Metrics]:
    """The four task inputs, keyed by capture, with the empty captures dropped."""
    v = _nonempty({cap: rows_v(cap, acts) for cap, acts in caps.items()})
    c = _nonempty({cap: rows_c(cap, acts) for cap, acts in caps.items()})
    lrows = _nonempty({cap: row_l(cap, acts, models) for cap, acts in caps.items()})
    p = {cap: seq_p(acts) for cap, acts in caps.items()}
    p = {k: s for k, s in p.items() if len(s) >= 3}
    return v, c, lrows, p


def _cohort_summary(caps: dict[str, list[Act]]) -> Metrics:
    models = _models_one_hot(caps)
    v, c, lrows, p = _cohort_rows(caps, models)
    return {
        "captures": len(caps),
        "models_one_hot": models,
        "V_verification_fails": {
            "captures": len(v),
            "rows": sum(len(r) for r in v.values()),
            **evaluate_event("V", v),
        },
        "C_command_fails": {
            "captures": len(c),
            "rows": sum(len(r) for r in c.values()),
            **evaluate_event("C", c),
        },
        "L_session_level": {"captures": len(lrows), **evaluate_level(lrows)},
        "P_next_activity_kind": {
            "captures": len(p),
            "transitions": sum(max(0, len(s) - 2) for s in p.values()),
            **evaluate_sequence(p),
        },
    }


def _print_cohort(name: str, found: Metrics) -> None:
    print(f"\n== {name}: {found['captures']} captures")
    for task in ("V_verification_fails", "C_command_fails"):
        t = found[task]
        pooled = t.get("pooled", {})
        print(
            f"  {task}: rows {t['rows']} positives {pooled.get('positives')}"
            f" base {pooled.get('base_rate')!s:.6} AUC {pooled.get('auc')!s:.6}"
            f" (persistence {pooled.get('auc_persistence')!s:.6})"
            f" Brier skill {pooled.get('brier_skill_vs_best_null')!s:.6}"
            f" -> {t['label']}"
        )
    for tname, t in found["L_session_level"].items():
        if tname in ("captures",):
            continue
        pooled = t.get("pooled", {})
        print(
            f"  L {tname}: n {pooled.get('n')}"
            f" MAE skill {pooled.get('mae_skill')!s:.6}"
            f" spearman {pooled.get('spearman')!s:.6}"
            f" median error ratio {pooled.get('median_abs_error_ratio')!s:.6}"
            f" -> {t['label']}"
        )
    t = found["P_next_activity_kind"]
    pooled = t.get("pooled", {})
    print(
        f"  P next kind: transitions {t['transitions']}"
        f" logloss base {pooled.get('logloss_base')!s:.6}"
        f" lag1 {pooled.get('logloss_lag1')!s:.6}"
        f" reduction {pooled.get('reduction_lag1')!s:.6}"
        f" (lag2 {pooled.get('reduction_lag2')!s:.6})"
        f" accuracy base {pooled.get('accuracy_base')!s:.6}"
        f" lag1 {pooled.get('accuracy_lag1')!s:.6} -> {t['label']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(DB))
    options = parser.parse_args()
    started = time.perf_counter()
    by_cap = load(Path(options.db))
    cohorts: dict[str, dict[str, list[Act]]] = collections.defaultdict(dict)
    for cap, acts in by_cap.items():
        cohorts[cohort_of(cap)][cap] = acts
    summary: dict[str, Any] = {
        "experiment": "E15",
        "db": options.db,
        "folds": FOLDS,
        "bars": BARS,
        "prefix": PREFIX,
        "min_requests": MIN_REQUESTS,
        "cohorts": {},
    }
    for name, caps in sorted(cohorts.items()):
        summary["cohorts"][name] = _cohort_summary(caps)
    summary["wall_s"] = round(time.perf_counter() - started, 3)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str)
    )
    for name, found in summary["cohorts"].items():
        _print_cohort(name, found)
    print(f"\nwall {summary['wall_s']} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
