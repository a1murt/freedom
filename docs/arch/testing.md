# Package "testing": offline judge, end-to-end process test, provider tests, CI

Audience: the organizers' technical check of the repository, the integration step, and every teammate who needs to run the product without a Claude key. Nothing here changes an existing endpoint, field or schema; the only runtime addition is a new provider name, `fake`.

| File | Role |
|---|---|
| `api/cqa/judge/providers/fake.py` | `FakeProvider` (name `fake`, model `fake-judge`): deterministic offline judge that reads the real prompt and returns a valid `Verdict` |
| `api/cqa/judge/providers/base.py` | one added branch: `get_provider("fake" \| "fake:<model>")` → `FakeProvider` (everything else unchanged) |
| `api/tests/test_e2e_flow.py` | full process test executed in a subprocess on a temporary SQLite database (12 tests) |
| `api/tests/test_providers.py` | fake judge, OpenAI-compatible adapter over `httpx.MockTransport`, Claude adapter with a fake SDK client (48 tests incl. parametrized, 2 documented xfails) |
| `.github/workflows/ci.yml` | `api` job (uv, ffmpeg, pytest) and non-blocking `web` job (pnpm typecheck + build) |
| `api/pyproject.toml` | `asyncio_default_fixture_loop_scope = "function"` (silences the pytest-asyncio deprecation warning; nothing else added) |

## 1. Offline judge `fake`

### Use

```python
from cqa.judge.providers import get_provider
judge = get_provider("fake")            # FakeProvider(model="fake-judge"); get_provider("fake:other") sets the model name
res = await judge.complete(bundle.system, bundle.messages, Verdict)   # same call as every JudgeProvider
```

- Any code path that builds providers through `get_provider` accepts it today, including the ensemble (`ensemble_members: [{"provider": "fake"}, {"provider": "fake", "model": "fake-judge-2"}]`).
- `POST /api/evaluations/run` with `"provider": "fake"` needs integration request 1 (one branch in `cqa.judge.runner._members`; until then the runner silently routes unknown names to Claude). The e2e test installs a thin shim when the branch is missing and warns about it.

```http
POST /api/evaluations/run
{"conversation_ids": ["e2e-ru-good", "e2e-ru-bad"], "checklist_version_id": 1, "provider": "fake", "mode": "full"}
→ 200 {"job_id": "j_f7dd89acbc2b", "queued": 2, "estimated_cost_usd": 0.36, "rejudge_codes": null, "checklist_version_id": 1}
```

The estimate still uses the Claude price per chat until integration request 2; the actual cost is 0.

Card produced by the fake judge (excerpt of `GET /api/conversations/e2e-ru-bad/evaluation?checklist_version_id=1`, after the reviewer changed C1):

```json
{"id": 1, "status": "completed", "provider": "fake", "mode": "single", "model": "fake:fake-judge",
 "total_score": 53.85, "final_total_score": 69.23, "critical_error": true, "cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
 "criteria": [
  {"code": "C1", "applicable": true, "answer": "no", "score": 0.0, "confidence": 0.9,
   "explanation": "Установление контакта — Нет. В первой реплике оператора нет приветствия. Основание — «Меня зовут Алексей. Что у вас?».",
   "evidence": [{"turn_idx": 3, "speaker": "operator", "quote": "Меня зовут Алексей. Что у вас?", "role": "supports", "offset_seconds": 50.0}],
   "checked_span": {"from_turn": 3, "to_turn": 3}, "flags": ["fake_judge"], "review_state": "changed", "final_answer": "yes"},
  {"code": "C2", "applicable": false, "na_reason": "Клиент не обсуждал свой счёт, поэтому уточнять потребность не требовалось.",
   "explanation": "Выявление потребности — N/A. Клиент не обсуждал свой счёт, поэтому уточнять потребность не требовалось.",
   "evidence": [{"turn_idx": 3, "speaker": "operator", "quote": "Меня зовут Алексей. Что у вас?", "role": "context"}], "flags": ["fake_judge"]},
  {"code": "C8", "applicable": true, "answer": "yes", "confidence": 0.9,
   "explanation": "Грубые ошибки — Да. Оператор оскорбил клиента. Основание — «Вы вообще читать умеете? Всё есть в приложении: …».",
   "evidence": [{"turn_idx": 5, "speaker": "operator", "quote": "Вы вообще читать умеете? Всё есть в приложении: «Меню → Отчёты и документы → Отчёт брокера».", "role": "supports"}],
   "flags": ["fake_judge"], "review_state": "confirmed", "final_answer": "yes"}]}
```

The trace (`GET /api/evaluations/{id}/trace`) stores `response = {"provider": "fake", "model": "fake-judge", "rule": "gross_insult", "stop_reason": "end_turn", "usage": {zeros}, "text": "<verdict JSON>", "parsed": {…}}`; `step_results` say what was checked (`"Стоп-фраза «читать умеете» (insult) в реплике [5]."`).

### How it works

1. Parses the messages exactly as `cqa.judge.packer.build_bundle` writes them: the transcript after `## Transcript` (`[idx] speaker (ts) <segment>: text`, no tag = operator segment; lines that are not turn lines continue the previous turn, so multi-line texts are reconstructed byte for byte), the structural facts JSON, and the criterion block (`# Criterion <code> · <title>`, `Allowed answers: binary: "yes" (Да), "no" (Нет)` or `scale, one of: "20", …`, `N/A: allowed…` / `N/A: not allowed…`). Retry and arbiter rounds only append messages; the last criterion block wins. String contents (OpenAI style) work too.
2. Applies one rule per criterion code (operator turns = operator segment; `<pre>`, `<internal>` and `<post>` lines are ignored, e.g. a line logged after "Клиент завершил разговор"):

| Code | Rule (RU / KZ / EN keywords) | Answer |
|---|---|---|
| C1 | first operator turn has a greeting (здравствуйте, добрый день, сәлеметсіз бе, hello, good afternoon…) | yes / no |
| C2 | N/A when the criterion allows N/A and no client/operator turn mentions the account (счёт, баланс, пополн-, зачисл-, вывод, шот, account, deposit…); otherwise an operator question mark | N/A / yes / no |
| C3 | same N/A rule; otherwise an operator asks for identifiers (ФИО, ИИН, дата рождения, код из SMS, аты-жөні, ЖСН, full name…), or a system event "Личность клиента подтверждена", or the mask `[Данные идентификации скрыты]` | N/A / yes / no |
| C4, C6, any scale criterion | top step (max of the listed values, whatever their order) | "20" / "10" |
| C5 | rude operator phrase (читать умеете, не мои проблемы, can you even read…) | no / yes |
| C7 | last operator turn says goodbye (до свидания, хорошего дня, всего доброго, сау болыңыз, have a good day, bye…) | yes / no |
| C8 | operator stop phrase «читать умеете» (insult), «на карту» (paid service via a card), «личный номер» (personal number), plus EN/KZ equivalents | yes / no |
| any other binary code (e.g. a new C9) | positive option | yes |

3. Returns a `Verdict`: evidence = the deciding operator turn with a verbatim quote (≤ 300 chars, window around the keyword), `checked_span` for missed actions, N/A evidence with role `context`, Russian explanation in the checklist style «<Критерий> — <Ответ>. <что найдено>. Основание — «цитата».», confidence 0.9 (keyword rule) / 0.7 (N/A) / 0.6 (default), `flags = ["fake_judge"]`, no claims. C8 explanations are phrased so `cqa.analytics.violations.classify` puts them in the right type (insult / paid_bypass), so the gross-violations view works offline.
4. Output is validated against the schema passed in (`schema.model_validate`), usage is all zeros, `cost()` is 0, `stop_reason = "end_turn"`. Schemas with `definition`/`decision_steps` (`JudgeSpec`) get `cqa.checklist.compiler.fallback_spec` of the criterion JSON embedded in the prompt; any other schema → `ProviderError`. A prompt without a criterion block → `ProviderError`.

Measured on the 65 DEV dialogs (local copy, PII-masked like the runner does): 520 verdicts, all valid; `cqa.judge.validate` reports no issue except its own hard rule that forces C7 to N/A after a connection loss (4 dialogs), exactly as it does for Claude.

## 2. End-to-end process test (`api/tests/test_e2e_flow.py`)

One module-scoped fixture runs the file itself as a script in a subprocess with `DATABASE_URL=sqlite:///<tmp>/e2e.db`, `DATA_DIR=<tmp>/data`, `ANTHROPIC_API_KEY=""`, `ORGANIZER_API_URL=""` and pinned guard settings (`TEAM_NAME`, `DAILY_BUDGET_USD`, `MAX_*`). The engine that `cqa.db` creates at import time therefore never sees `api/data/cqa.db`, and the job worker threads die with the subprocess. The script refuses to start (guard) when the URL is not an isolated SQLite file, points at the demo database, or a Claude key is visible. It drives the real app through `TestClient(app)` with lifespan (tables, seed checklist, start-up hooks), prints one ASCII JSON line `E2E_SUMMARY={…}`, and 12 pytest tests assert on it.

| Step | Calls | Asserted |
|---|---|---|
| import | `POST /api/conversations/upload` (organizer payload `{"isSuccess", "data": {"records": [2 chats]}}`) as analyst | 2 imported, operator names Алина / Алексей detected, turn counts |
| evaluate | dry run, then `POST /api/evaluations/run` provider `fake`, poll `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events` | dry run creates no job; job completed 2/0, cost 0; SSE `text/event-stream` with 16 `criterion.done`, 2 `evaluation.done`, `job.done` |
| card | `GET /api/conversations/{id}/evaluation`, `/trace` | 8 criteria, provider/model, zero tokens and cost, every evidence turn exists with the right speaker and cites an operator turn, Russian explanations, no validation flags, totals equal an independent recomputation (100.0 and 53.85), weights sum to 100, sections sum to the total |
| appeal | operator Алина on Алексей's dialog → 403 `not_your_dialog`; Алексей appeals C1 (headers `X-CQA-Role: operator`, `X-CQA-User: %D0%90…`, `X-Request-ID`); duplicate → 409 `already_open`; preview; reviewer changes C1 → yes; second decision → 400 `invalid_decision`; analyst disputes C8, reviewer confirms | review states, total kept at 53.85, final total 69.23 = preview = persisted = independent recomputation, sections recomputed, confirm keeps 69.23 and the critical flag |
| roles (when the middleware is active) | unknown role probe → 400 `unknown_role`; `GET /api/me`; operator decides / visitor runs / operator pastes | 403 `forbidden` each; author filled from the headers (the foreign appeal sends no author fields) |
| views | `GET /api/violations`, `/api/operators`, `/api/analytics/summary`, CSV, PDF | one insult on turn 5, confirmed by the reviewer; operator means 100.0 / 69.23 and critical counts; reports render |
| checklist v2 | `POST /api/checklists/{id}/versions` (v1 criteria + new C9, weight 5, `compile_specs: false`) as methodologist; diff | added `["C9"]`, nothing changed, rejudge `["C9"]` |
| partial | `POST /api/evaluations/run` mode `partial`, base v1 | only C9 traced; C1..C8 copied; totals re-normalised to 100 over 105 weight points (100.0; 57.14 for the bad chat) |
| export | `cqa.export.results_json.from_db` + `validate_document`, CLI `main(["--db", …])` and `main(["--validate", …])`, `GET /api/export/results.json`, `GET /api/export/results/validate` | no errors, JSON 1.0 values (true/false/null, raw scale steps), 1-based evidence turns, CLI prints VALID and returns 0, API file identical to the builder, `X-Results-Valid: true` |
| audit (when `/api/audit` and the middleware exist) | `GET /api/audit?limit=500` | `conversation.upload`, `evaluation.run`, `appeal.create`, `appeal.decide`, `checklist.version.create` recorded with the URL-decoded actor; the appeal event carries `X-Request-ID` and the appeal id |
| error envelope | `GET /api/evaluations/999999` | `{"error": {"code": "not_found", "message": …}}` |

Run the flow by hand (prints the summary): `cd api && DATABASE_URL=sqlite:////tmp/cqa-e2e/e2e.db DATA_DIR=/tmp/cqa-e2e ANTHROPIC_API_KEY= .venv/bin/python tests/test_e2e_flow.py`.

Current state (with the roles, audit and platform packages present): 12 passed. Without the roles middleware or the audit router the two dependent tests skip with a reason.

## 3. Provider tests (`api/tests/test_providers.py`)

- Autouse fixture: `settings.anthropic_api_key = None` and `anthropic_provider._client = None`, so no test can reach the Claude API.
- Fake judge (16 test functions, 22 cases): registration (`fake`, `fake:<model>`, other names unchanged, unknown still raises); every criterion on RU / KZ / EN chats and an RU call passes `validate_verdict` with zero issues; heuristics per code incl. N/A only when allowed, identification via request / system event / mask, scale top step with ascending options, unknown codes; C8 explanations classified by `violations.classify`; determinism across instances; string contents, retry and arbiter message shapes; multi-line turns; parse errors; JudgeSpec; `judge_one` (one trace, cost 0) and a two-member ensemble (votes, no disagreement).
- `OpenAICompatibleProvider` (9 functions, 13 cases) over `httpx.MockTransport` (module attribute `openai_compatible.httpx` replaced by a proxy whose `AsyncClient` gets the mock transport): request shape (URL, bearer key, model, temperature 0, `max_tokens`, `response_format` json_schema `Verdict`, flattened content blocks, schema reminder), usage mapping, cost 0; 400 mentioning `response_format` → second call without it; other 400 → error after one call; retry failure; `<think>` blocks (also containing braces), prose and code fences stripped; 404/500/503 → `ProviderError` with the status (transient codes stay detectable by the runner); no JSON → `ProviderError`.
- `AnthropicProvider` (8 functions, 13 cases) with a duck-typed client in `anthropic_provider._client`: exact `messages.parse` kwargs (cached system block, `output_format`, adaptive thinking, effort), parsed model or dict, usage incl. cache read/write and missing fields, raw trace text, cost from `settings.rate_for`; `stop_reason="refusal"` → `ProviderRefusal`; no parsed output → `ProviderError`; `BadRequestError` 400, `AuthenticationError` 401, `RateLimitError` 429, `InternalServerError` 500, `OverloadedError` 529 built with the SDK constructor `APIStatusError(message, *, response, body)` on the SDK's own HTTP library (`httpx2` in anthropic 2.6, picked from `anthropic._exceptions`), mapped to `"Anthropic API error <code>: <message>"` with `__cause__`; `APIConnectionError(request=…)` / `APITimeoutError(request)` → connection error; no key → error without building a client; `cqa.judge.runner._call_once` retries a 429 once (sleep patched) and does not retry a 400.
- Two `xfail(strict=False)` tests document real gaps in `openai_compatible.py` (integration request 4); they will report XPASS once fixed.

## 4. CI (`.github/workflows/ci.yml`)

- Triggers: `push`, `pull_request`. `permissions: contents: read`; superseded runs of the same ref are cancelled.
- `api` (ubuntu-latest, 20 min): `actions/checkout@v4`, `apt-get install ffmpeg`, `astral-sh/setup-uv@v6` (cache keyed on `api/uv.lock`, `UV_PYTHON=3.12`), `uv sync --extra dev`, `uv run --extra dev pytest -q`. Env `ANTHROPIC_API_KEY=""`, `ORGANIZER_API_URL=""`: no secrets are needed or used.
- `web` (ubuntu-latest, 15 min, `continue-on-error: true` while the frontend is being redesigned): `pnpm/action-setup@v4` (pnpm 10, matching the local 10.x and lockfile v9), `actions/setup-node@v4` (Node 22, pnpm cache on `web/pnpm-lock.yaml`), `pnpm install --frozen-lockfile`, `pnpm typecheck`, `pnpm build`.
- Verified locally by a clean-room run: `api/` copied to a scratch folder (no `.venv`, no data, no repo `.env`), `uv sync --extra dev --offline` from the lock, full suite green, no asyncio warning. The web job was not run (frontend under redesign, `web/` untouched).

## 5. Permissions, audit, guards

The fake provider needs no permission of its own: running it is `evaluations.run` like any provider, and every run passes the platform guards (budget, jobs, dialogs). The e2e test uses the contract headers (`X-CQA-Role`, URL-encoded `X-CQA-User`, `X-Request-ID`) with roles analyst, operator, reviewer, methodologist, director and visitor; with no headers it would run as `demo`.

## 6. Limitations

- The fake judge is a plumbing double, not a quality judge: keyword rules miss paraphrases, flag legitimate phrases («вывод на карту» → C8), and C4/C6 are always the top step. Its scores mean nothing for calibration or the organizer results.
- It reads the prompt format of `packer.py` / `prompts/v1/criterion.md` (header, `Allowed answers:`, `N/A:` lines). If the ML teammate changes those lines, the fake raises `ProviderError` rather than guessing; the tests catch it.
- The e2e flow uses two RU chats; KZ / EN / call coverage of the fake lives in the unit tests.
- The CI web job only typechecks and builds; there are no frontend tests.

## 7. Observations from the e2e run (input for the integration step)

1. `cqa.judge.runner._members` routes any unknown provider name to Claude, so `provider: "fake"` from the API would call (and bill) Claude, or fail without a key. The e2e shim covers it for now (integration request 1).
2. Partial re-evaluation copies the AI answers but not the reviewer decision: after C1 was changed to «Да» in v1, the v2 evaluation of the same dialog shows C1 = «Нет», `review_state = "none"`, total 57.14 instead of 71.43. This is the same gap as docs/arch/checklists.md integration request 4 (`test_checklist_governance.py` xfail). The e2e test records it in `partial.bad_c1_carried_review` and accepts both totals.
3. `results.json` exports the AI answers (`answer`), not the reviewer-final ones: the reviewed dialog is exported with 53.85, not 69.23. Policy decision for the shared `results_json.py` (request 6); the e2e accepts both.
4. The roles middleware also records refused writes (status 403, summary «Отказано: …») besides successful ones; that is a superset of contract §2 and is kept as information in the summary (`audit.denied`).

## 8. Integration requests (shared or other packages' files)

1. `api/cqa/judge/runner.py`, `_members`, before the `local` branch:
   ```python
   if provider_name == "fake":
       return [get_provider("fake", model)]
   ```
   Checked on a scratch copy: with it the e2e flow routes `fake` natively (12 passed with `-W error::UserWarning`, no shim).
2. `api/cqa/judge/runner.py`, `estimate_cost`: add `"fake": 0.0` to both the per-chat and per-call dictionaries, so offline runs are estimated at $0 and are never blocked by the daily budget guard (`cqa/jobs/guards.py` already exempts $0 estimates).
3. `api/cqa/export/results_json.py`, `model_block` (optional): count provider prefix `fake` as `local` (offline), e.g. `prov in ("local", "ollama", "vllm", "openai_compatible", "fake")`. Today a fake-judged file says `{"mode": "external_api", "name": "fake-judge"}`. Fake results are never meant for the organizers, so this is cosmetic.
4. `api/cqa/judge/providers/openai_compatible.py`: (a) in `_extract_json`, wrap the final `json.loads(m.group(0))` and raise `ProviderError("Local model returned invalid JSON: …")` instead of letting `json.JSONDecodeError` escape; (b) around `c.post(...)`, catch `httpx.HTTPError` and raise `ProviderError(f"Local provider connection error: {e}") from e`, so the runner retries it as transient ("connection"). Then drop the two `xfail` markers in `tests/test_providers.py`.
5. Partial re-evaluation and reviewer decisions: see docs/arch/checklists.md request 4 (observation 2).
6. Decide whether `results.json` should carry reviewer-corrected values (`final_answer` and its applicability) instead of the AI answers (observation 3). If yes, change `build_record` in the shared `results_json.py` and keep the validator as is.
7. `docs/API.md`, `POST /api/evaluations/run`: provider enum `"anthropic|local|ensemble|fake"` with the note "`fake` = offline deterministic judge (tests, CI, keyless demos), cost 0, `model` defaults to `fake-judge`".
8. `docs/ARCHITECTURE.md` §7.1 providers table: row `fake | fake-judge | Offline test double | keyword heuristics over the real prompt, valid Verdict, no network, cost 0`.
9. `README.md`, Tests line: `cd api && uv run --extra dev pytest -q` (offline, includes the end-to-end process test) and the CI workflow `.github/workflows/ci.yml`; optionally a status badge once the repository is on GitHub.
