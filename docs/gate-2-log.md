# Gate-2 log

Append-only receipts of `mutate-verify.sh` (governance programme F8.1). One row per mutation:
the suite was run on a pristine copy of this repo, the source was then broken on purpose, and
the suite was run again. `PASS` = the mutant was killed, the tests noticed. `SURVIVOR` = the
tests stayed green while the code was broken — a statement about the tests, not about the code.

`guard-build.sh` reads this file at `git push`, so **only `mutate-verify.sh` writes it**:
`guard-pipeline-write.sh` denies `Write`/`Edit` here. A row is never edited or removed.

**Commit this file.** `guard-build.sh` reads it from disk, so a log that is never committed is
lost to the next clone or `git clean` — and the repo then un-adopts the gate silently, which is
the one failure mode nothing else would announce. The monthly report counts adopting repos for
exactly that reason.

Link it from the repo TRACKER too (the `gate-2:` field of the `Pipeline:` line is the natural
place) — otherwise `md-lint --reach` counts it as an orphan, one per adopting repo.

| Date | Commit | What was broken | Mutation | Suite | Verdict |
|---|---|---|---|---|---|
| 2026-09-22 16:31 | fd4622d14604356af40230fb64bb1e322bd5c960-dirty | ok always true on the success path (breaks the ok/checked invariant) | sed -i '' 's/        ok = checked and lag_count == 0 and drift_count == 0/        ok = True/' src/lag_check.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-22 16:32 | fd4622d14604356af40230fb64bb1e322bd5c960-dirty | absent pair seeded as None instead of 0 — a real oversell would read clean | sed -i '' 's/                result\[pair\] = 0/                result[pair] = None/' src/db.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-22 16:32 | fd4622d14604356af40230fb64bb1e322bd5c960-dirty | failed chunk seeded as 0 instead of None — could-not-check becomes no-stock | sed -i '' 's/                    result\[pair\] = None/                    result[pair] = 0/' src/db.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-23 10:27 | 334a608ffbcc4321867af80937847a17b18cd95a | MAX_REPORTED_ENTRIES raised to 10000 — the serialisation cap stops bounding the payload | sed -i '' 's/^MAX_REPORTED_ENTRIES = 50/MAX_REPORTED_ENTRIES = 10000/' src/lag_check.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-25 13:51 | 5d8bc6aa28d727f8faa148a57eed060871ac9a8b | ok always true on the success path (breaks the ok/checked invariant) | sed -i '' 's/        ok = checked and lag_count == 0 and drift_count == 0/        ok = True/' src/lag_check.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-25 13:51 | 5d8bc6aa28d727f8faa148a57eed060871ac9a8b | absent pair seeded as None instead of 0 — a real oversell would read clean | sed -i '' 's/                result\[pair\] = 0/                result[pair] = None/' src/db.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-25 13:51 | 5d8bc6aa28d727f8faa148a57eed060871ac9a8b | failed chunk seeded as 0 instead of None — could-not-check becomes no-stock | sed -i '' 's/                    result\[pair\] = None/                    result[pair] = 0/' src/db.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-25 13:51 | 5d8bc6aa28d727f8faa148a57eed060871ac9a8b | MAX_REPORTED_ENTRIES raised to 10000 — the serialisation cap stops bounding the payload | sed -i '' 's/^MAX_REPORTED_ENTRIES = 50/MAX_REPORTED_ENTRIES = 10000/' src/lag_check.py | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
| 2026-09-25 13:51 | 5d8bc6aa28d727f8faa148a57eed060871ac9a8b | last-sync read reverted to DATE_FORMAT with %% beside a bound param — the 2026-09-23 production defect | /usr/bin/python3 -c "import pathlib; p=pathlib.Path('src/db.py'); s=p.read_text(); n=s.replace('SELECT UNIX_TIMESTAMP(sjl.executed_at) AS', 'SELECT DATE_FORMAT(sjl.executed_at, ' + chr(39) + '%%Y-%... | /usr/bin/python3 -m pytest -o addopts="" -q | PASS |
