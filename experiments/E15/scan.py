"""Quick, pre-registration-free SCAN of where structure might exist.

Numbers only; no claims. Reads the E13 store copy read-only.
"""

import collections
import itertools
import math
import sqlite3
import statistics

con = sqlite3.connect("experiments/E13/out/home/telltale.db")


def q(sql: str, *params: object) -> list[tuple]:
    return con.execute(sql, params).fetchall()


# a. command success observability
print(
    "a. command success field:",
    q(
        "select json_extract(fields,'$.success'), count(*) from activities"
        " where activity_type='command' group by 1"
    ),
)
# b. verification transitions within a capture (previous result -> this result)
rows = q(
    "select capture_id, started_at, json_extract(fields,'$.success') from activities"
    " where activity_type='verification_run'"
    " and json_extract(fields,'$.success') is not null"
    " order by capture_id, started_at"
)
trans = collections.Counter()
prev = {}
for cap, _ts, ok in rows:
    if cap in prev:
        trans[(prev[cap], ok)] += 1
    prev[cap] = ok
tot = sum(trans.values())
base_fail = sum(1 for r in rows if r[2] == 0) / len(rows)
p_ff = trans[(0, 0)] / (trans[(0, 0)] + trans[(0, 1)])
p_pf = trans[(1, 0)] / (trans[(1, 0)] + trans[(1, 1)])
print(
    f"b. verification runs {len(rows)}, base fail rate {base_fail:.3f};"
    f" transitions {dict(trans)}; P(fail|prev fail)={p_ff:.3f}"
    f" P(fail|prev pass)={p_pf:.3f}"
)
# c. cross-sectional: early prefix vs total per session (claude, >=32 requests)
caps = q(
    "select capture_id, count(*) from activities where activity_type='model_request'"
    " group by 1 having count(*)>=32"
)
tok = {}
for cap, n in caps:
    rs = q(
        "select json_extract(fields,'$.output_tokens'),"
        " json_extract(fields,'$.input_tokens'),"
        " json_extract(fields,'$.cache_read_tokens') from activities"
        " where activity_type='model_request' and capture_id=? order by started_at",
        cap,
    )
    out = [r[0] or 0 for r in rs]
    tok[cap] = (
        n,
        sum(out[:16]),
        sum(out),
        statistics.median(out[:16]) if out[:16] else 0,
        statistics.median(out[16:]) if out[16:] else 0,
        (rs[15][2] or 0) if len(rs) > 15 else None,
    )


def spearman(xs, ys):
    def rank(v):
        s = sorted(range(len(v)), key=lambda i: v[i])
        r = [0] * len(v)
        for k, i in enumerate(s):
            r[i] = k
        return r

    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    vx = sum((a - mx) ** 2 for a in rx)
    vy = sum((b - my) ** 2 for b in ry)
    return cov / math.sqrt(vx * vy)


vals = list(tok.values())
req_median = statistics.median(v[0] for v in vals)
req_p90 = sorted(v[0] for v in vals)[int(0.9 * len(vals))]
print(
    f"c. sessions >=32 requests: {len(vals)}; requests per session"
    f" median {req_median}, p90 {req_p90}"
)
s1 = spearman([v[1] for v in vals], [v[2] - v[1] for v in vals])
s2 = spearman([v[3] for v in vals], [v[4] for v in vals])
s3 = spearman([v[5] or 0 for v in vals], [v[0] for v in vals])
print(f"   spearman(first-16 output tokens, total remaining output tokens) = {s1:.3f}")
print(
    "   spearman(median output tokens first 16, median output tokens after 16)"
    f" = {s2:.3f}"
)
print(f"   spearman(context size at request 16, total requests) = {s3:.3f}")
# d. activity kind after each request: lag-1 structure
kinds = q(
    "select capture_id, started_at, activity_type from activities where activity_type"
    " in ('file_edit','file_read','command','verification_run','model_request')"
    " order by capture_id, started_at"
)
seq = collections.defaultdict(list)
for cap, _ts, k in kinds:
    seq[cap].append(k)
base = collections.Counter()
trans = collections.Counter()
for ks in seq.values():
    ev = [k for k in ks if k != "model_request"]
    for a, b in itertools.pairwise(ev):
        trans[(a, b)] += 1
        base[b] += 1
tot = sum(base.values())
H0 = -sum(c / tot * math.log2(c / tot) for c in base.values())
H1 = 0
for a in base:
    row = {b: trans[(a, b)] for b in base}
    s = sum(row.values())
    if s:
        H1 += s / tot * -sum(c / s * math.log2(c / s) for c in row.values() if c)
print(
    f"d. tool-activity kinds: base rates {dict(base)}; entropy base {H0:.3f} bits,"
    f" conditional on previous kind {H1:.3f} bits"
    f" (reduction {100 * (1 - H1 / H0):.1f}%)"
)
for a in base:
    row = {b: trans[(a, b)] for b in base}
    s = sum(row.values())
    print(f"   after {a:16} -> " + ", ".join(f"{b} {row[b] / s:.2f}" for b in base))
