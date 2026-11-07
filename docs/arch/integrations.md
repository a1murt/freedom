# Package "integrations": Dynamics 365 Contact Center

Owner: integrations work package. Admin guide for the Dynamics 365 side (format, sources, flow, form tab, CSP/CORS, personal data): [`docs/INTEGRATION_D365.md`](../INTEGRATION_D365.md).

## What was built

| File | Content |
|---|---|
| `api/cqa/integrations/__init__.py` | Package exports |
| `api/cqa/integrations/dynamics.py` | `parse_transcript()`: Omnichannel transcript → organizer record. Also `decode_documentbody()`/`encode_documentbody()`, `detect_language()`, the `normalize_language/channel/conversation_id/role()` helpers, `content_hash()`, `enrich_conversation()` (post-upsert offsets, duplicates, first response, meta) and `sample_messages()` (a synthetic transcript) |
| `api/cqa/routers/integrations.py` | `POST /api/integrations/dynamics/transcripts`, `GET /api/integrations/dynamics/manifest` |
| `api/tests/test_dynamics.py` | 51 test functions, 58 test cases |
| `docs/INTEGRATION_D365.md` | Admin guide |
| `docs/arch/integrations.md` | This file |

No shared file was edited. `main.py` already loads `cqa.routers.integrations` (router list), and `cqa.security.roles.ROUTE_MAP` already maps the POST route. Verified live: `cqa.main:app` on port 8016 with a scratch SQLite database (both routes mounted, operator → 403 from the middleware, audit rows written, CORS preflight with the custom headers).

## Endpoints

### `POST /api/integrations/dynamics/transcripts`

Permission `integrations.ingest` (enforced by the security middleware through `ROUTE_MAP` and again in the handler), plus `evaluations.run` when `evaluate: true` (handler only). Audit action `integration.dynamics.ingest`. Body is parsed by hand (async handler, then a threadpool), so even malformed JSON gets the error envelope. The model `DynamicsTranscriptIn` (extra fields allowed) documents and validates the fields in OpenAPI.

Request (one transcript source: `annotation` | `documentbody` | `transcript` | `messages`):

```json
{
  "conversation_id": "7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
  "annotation": {"annotationid": "3a5c7e90-…", "objecttypecode": "msdyn_transcript", "_objectid_value": "9b1d3f50-…",
                 "documentbody": "W3siaWQiOiAiMTc5Mzk1NTYwMDAwMCIsICJjb250ZW50IjogItCX0LTRgNCw…"},
  "evaluate": true,
  "checklist_version_id": null,
  "provider": null
}
```

Optional fields:

| Field | Values |
|---|---|
| `language` | `RU`/`KZ`/`EN`, BCP-47 tags, LCIDs |
| `channel` | `agent chat`/`voice call`, words, D365 codes |
| `model` | Model name |
| `force` | Re-evaluate unchanged content |
| `overwrite` | Replace a non-D365 conversation with the same id |
| `audio_url` | http(s) recording link |
| `private_messages` | `system` \| `skip` |
| `control_messages` | `events` \| `all` \| `none` |
| `order` | `auto` \| `keep` \| `chronological` |
| `participants` | `{name or id: role}` |

Any Dataverse column may ride along: `msdyn_ocliveworkitemid`, `_msdyn_liveworkitemidid_value`, `msdyn_channel`, …

Response `200`:

```json
{
  "conversation_id": "7f0c2a4e-1b3d-4c5e-9f60-00000000d365", "turns": 7, "language": "RU", "job_id": "j_3fa94c0b12de",
  "card_url": "https://cqa-freedom-pi.vercel.app/#/conversations/7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
  "embed_url": "https://cqa-freedom-pi.vercel.app/?embed=1&conversation=7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
  "channel": "agent chat", "created": true, "operator_name": "Алина", "first_response_seconds": 22.0, "dataset_id": 1,
  "language_detected": true, "speakers": {"client": 2, "bot": 1, "system": 2, "operator": 2},
  "d365": {"liveworkitem_id": null, "transcript_id": "9b1d3f50-…", "annotation_id": "3a5c7e90-…", "conversation_id_source": "conversation_id",
           "channel_source": "default", "source_field": "annotation.documentbody", "messages_total": 7, "skipped": {},
           "internal_messages": 0, "order": "kept", "agents": ["Алина"]},
  "warnings": [],
  "evaluation": {"status": "started", "job_id": "j_3fa94c0b12de", "checklist_version_id": 1, "provider": "anthropic", "model": null,
                 "estimated_cost_usd": 0.18}
}
```

`evaluation.status` is one of:
- `not_requested`
- `started`
- `skipped_unchanged` (with `evaluation_id`, `existing_job_id`, `evaluation_status`): same content fingerprint as the previous push, and a queued/running/completed evaluation or an active job exists for this checklist version.

`job_id` is non-null only when a job was started. The web base comes from env `WEB_BASE_URL`, default `https://cqa-freedom-pi.vercel.app`.

Order of work:
1. Integration key and role checks.
2. Body parse and validation; transcript parse.
3. Conflict check (409 if the id belongs to a non-D365 conversation and `overwrite` is not set).
4. With `evaluate`: checklist version and provider validated (400/404/422) **before anything is written**.
5. Upsert through `cqa.ingest.service.upsert_record` into dataset `D365`; `enrich_conversation`; commit.
6. Under `cqa.jobs.runner.start_lock`: dedupe, then `cqa.jobs.guards.check_run(db, 1, estimate)`, then `start_evaluation_job([id], version, provider, model, mode="full", extra_params={"estimated_cost_usd", "source"})`.

Guard refusals return the platform's 429 envelope (`budget_exceeded` / `too_many_jobs`, `Retry-After` when the platform computes one) merged with `ingested: true, conversation_id, turns, language, card_url, embed_url`. The transcript is kept. A failing job start returns 503 `evaluation_not_started` with the same fields. The full error table is in the admin guide §3.

### `GET /api/integrations/dynamics/manifest`

Permission `read`. Contains no secrets. JSON with:
- `api_base_url` (env `PUBLIC_API_URL`, else `X-Forwarded-Proto/Host`, else the request URL) and `web_base_url`;
- `endpoints` and `links` (card and embed templates);
- `web_resource`:
  - `build_command: "pnpm build:single"`, `working_directory: "web"`, `output: "dist-single/index.html"`, suggested name `cqa_/card/index.html`;
  - `config_snippet` (`<script>` that maps D365's `id`/`data` parameters to `window.CQA_CONFIG = {apiUrl, embed: true, conversation}`);
  - `loader_html` (no-edit alternative via the URL hash);
  - `d365_parameters`, `size_note`;
- `form_tab` (table `msdyn_ocliveworkitem`, eight steps);
- `iframe_alternative` (OnLoad `form_script` for `IFRAME_cqa_card`);
- `power_automate`:
  - `trigger`: Dataverse "When a row is added, modified or deleted" on Notes, filter `objecttypecode eq 'msdyn_transcript'`;
  - `steps`;
  - `http_action` (headers and body with Power Automate expressions);
  - `retry_policy`;
  - `example_payload`: a working request with a base64 synthetic transcript, `evaluate: false` (tested by posting it);
  - `example_curl`;
- `transcript_format`, `security` (permission, integration key required or not, CORS origins needed, CSP and personal-data notes), `limitations`.

## Data and model notes

- **No new tables or columns.** Conversations and turns go through `upsert_record`, so normalization (segments, duplicates, operator name, terminal events) is the same as for organizer data.
- **Dataset.** One `Dataset(group_id="D365", source="dynamics365")`, created on the first push. `record_count` = the conversations currently in it. Conversations can be filtered by it, and `results.json?group_id=D365` works.
- **`Conversation.meta`** additionally holds `source: "dynamics365"` and `d365`:
  - `content_hash` (sha256 of channel + language + turns, used to skip re-evaluation), `ingested_at`;
  - `source_field`, `messages_total`, `turns`, `skipped` counts, `internal_messages`, `private_ignored`, `order`;
  - `language_detected`, `channel_source`, `conversation_id_source`, `liveworkitem_id`, `transcript_id`, `annotation_id`;
  - `agents` (agent display names, most messages first), `speakers`.
  No transcript text and no customer display names are stored in meta.
- **Turns.** Timestamps are kept as sent (ISO). Because `normalize()` parses chat clocks only as `MM:SS`, `enrich_conversation` fills chat `offset_seconds` from the ISO timestamps (seconds since the first message), re-runs the normalizer's duplicate rule (same speaker and text within 2 s) and computes `first_response_seconds` the normalizer's way (first operator turn minus the escalation / "agent joined" anchor). Calls are untouched, since `normalize()` already handles ISO for them. If `normalize()` starts filling chat offsets, the enrichment does nothing: it acts only when every offset is empty.
- **`operator_name`** stays the normalizer's greeting-detected name. The judge receives it as `operator_name_detected` (C1 evidence), so the D365 display name must not be written there (see integration request 4).
- **Rendered events.** `<addmember>`/`<deletemember>` of agents and the customer become RU/KZ/EN system texts. "Агент присоединился к беседе", "Agent joined the conversation" and "Агент қосылды" match `AGENT_JOINED_RE`, which gives the escalation anchor for chats and the operator segment start for calls. The customer texts avoid the terminal-event phrases ("клиент покинул", "customer left") so that a customer closing the window after a farewell is not read as `client_left`.
- **`Job.params`** gets `estimated_cost_usd`, which `cqa.jobs.guards` counts as reserved budget, and `source: "dynamics365"`. They are passed only when the runner accepts `extra_params`; the signature is inspected, so the older runner works too.
- **Config** read by the router: env `WEB_BASE_URL`, `PUBLIC_API_URL`, `INTEGRATIONS_API_KEY` (optional shared secret, header `X-CQA-Integration-Key`, compared in constant time). The env value wins; a `settings` field of the same name (lowercase) is used if the integration step adds one.

## Parsing rules (summary)

- **Inputs:**
  - `{annotation: {documentbody}}`, `{documentbody}` (any key case);
  - the decoded JSON (list, wrapper object, JSON string, JSON encoded twice);
  - a plain list (D365 messages or simplified `{speaker|role, text|content, timestamp}`, organizer records included).
  base64 may be standard or URL-safe, unpadded, line-wrapped, `data:`-prefixed or gzip-compressed, over UTF-8 (with or without BOM) or UTF-16. Attachment notes (`objecttypecode = msdyn_ocliveworkitem`) are rejected with `not_a_transcript`.
- **Speakers:**
  - Customer-like names (`Customer`, `Клиент`, `Visitor`, `Guest`…), the `FromCustomer`/`ChannelId-lcw` tags or a `phone`/`guest`/`device` identity → `client`.
  - `public` tag or an `8:orgid:<id>` `<addmember>` target → `operator`.
  - `application` identity → `bot`, except `DynamicsBot` and tag `system` → `system`. `from: null` → `system`.
  - Control messages → `system` events. Agent messages tagged `private` → `system` "[internal] name: text", or dropped with `private_messages=skip`. A customer's `private` tag is ignored.
  - Authors without a signal → `operator`, but when no client was found the earliest unresolved author is the client. With every user labelled agent and at least two of them, the earliest one that is not an `orgid` agent becomes the client. `participants` overrides everything.
  - If every agent message is `private`, the tag is ignored (`private_ignored: true`, warning).
- **Order:** kept, reversed when the whole list is newest-first (`auto`), `keep`, or `chronological` (stable sort).
- **Language:** the request value, else `detect_language()` over customer and agent text (bot text, then system text as fallbacks). `KZ` when Kazakh-specific letters are ≥ 2 % of the Cyrillic letters, other Cyrillic → `RU`, otherwise `EN`.
- **Channel:** the request value, else `msdyn_channel`/`msdyn_streamsource` 192370000 (Voice) or 192440000 (Voice call), a `phonecall` transcript target, `isVoice`, or `audio_url` → `voice call`. Otherwise `agent chat`.
- **Id:**
  1. `conversation_id`;
  2. `msdyn_ocliveworkitemid`, `_msdyn_liveworkitemidid_value`, `msdyn_liveworkitemidid`, `liveworkitemid`;
  3. `activityid`;
  4. the transcript id (`msdyn_transcriptid`, `_objectid_value`);
  5. `d365-<sha1 of the turns>`.
  GUIDs are lowercased without braces; other ids must match `[A-Za-z0-9][A-Za-z0-9._:@-]{0,63}`.
- **Text:** HTML stripped and entities decoded, literal `\n` from double-escaped exports collapsed, adaptive/hero-card text extracted for bots, attachments as `[вложение: name]`, deleted and empty messages dropped. Messages are capped at 10 000 characters and transcripts at 5000 messages.

## Permissions used

| Action | Permission | Where enforced |
|---|---|---|
| POST transcripts | `integrations.ingest` (demo, analyst, lead, admin) | `cqa.security` middleware (`ROUTE_MAP`) and the handler (`cqa.security.roles.current_actor` + `has_permission`, so `X-CQA-Role` is honoured even in an app without the middleware; contract fallback matrix if `cqa.security` cannot be imported) |
| POST with `evaluate: true` | `evaluations.run` as well | Handler |
| GET manifest | `read` | Everyone |

## Tests

`cd api && .venv/bin/python -m pytest -q tests/test_dynamics.py`: 58 cases, about 0.7 s. In-memory SQLite (`StaticPool`) per test with `dependency_overrides[get_db]`. `cqa.jobs.runner.start_evaluation_job` is monkeypatched with a fake that records the call and writes a queued `Job` row, so no model is called and `api/data/cqa.db` is never touched (checked by comparing the file's mtime and size before and after). The platform guards (`cqa.jobs.guards`) and the security roles run for real.

Coverage:
- **Mapping:** the documented message shape end to end (speakers, internal notes, HTML, attachment, deleted, control events, timestamps, stats); customer `private` tag; skip/all/none modes; the all-private valve; unlabelled authors; `public` and `orgid` signals; `FromCustomer` and phone identities; `participants`; simplified and organizer items; adaptive cards; epoch-id timestamps.
- **Decoding:** 18 documentbody variants giving the same record; decode errors; every parse error code; size limits.
- **Ids, channel, language, order:** all id sources and the GUID normalization; channel detection and normalization; language rules (KZ share, Kazakh name in Russian text, bilingual bot) and normalization; auto/keep/chronological order.
- **Endpoint:** ingest without evaluation (response, DB state, dataset, offsets, first response, no customer names); `WEB_BASE_URL`; upsert on re-post; voice call; duplicates on real timestamps; warnings; 409 and overwrite.
- **Evaluation:** job start (arguments including `extra_params`); the old runner signature; provider selection (`local`, `anthropic:<model>`, the Settings default, unknown → 422 with nothing written); missing checklist; dedupe (queued job, completed evaluation, `force`, changed content); failed evaluation retried; budget and concurrency through `cqa.jobs.guards`; the local guard fallback; job-start failure → 503.
- **Requests and access:** request errors (invalid JSON, empty, non-object, field validation, base64, audio URL, language, attachment note, 413); role matrix (8 cases); the separate `evaluations.run` check; header-only enforcement and `unknown_role`; fallback permissions; integration key.
- **Manifest:** contents, posting the example payload, `PUBLIC_API_URL` and forwarded headers; the OpenAPI request body.

The full suite (`cd api && .venv/bin/python -m pytest -q`) passes.

## Limitations

- Voice transcripts stored as files on `msdyn_transcript` (`msdyn_voicetranscript*`) are not parsed. Calls can be pushed as `messages` + `channel: "voice"` + `audio_url`.
- Speaker inference relies on the documented fields. Undocumented deployments may need `participants`. The Microsoft samples are partly inconsistent (a customer message tagged `private` with the agent's id); the rules above are built to survive that.
- The dedupe and the guards are atomic within one process only (`start_lock`). Two simultaneous first pushes of a brand-new id collide on the primary key; the loser gets 409 `concurrent_update` and no job.
- Attachments are not downloaded; only their names are listed.
- The CQA_CONFIG snippet has to be pasted into the built file, or the loader page used, until the web app reads D365's `id`/`data` parameters itself (integration request 7).

## Integration requests

1. **`api/cqa/audit/service.py` (audit, privacy, high priority).**
   - Add `"documentbody"` to `TRANSCRIPT_KEYS`.
   - In `compact()`, also omit Mapping values of transcript keys, and add `"participants"`, whose dict keys can be customer names:
     ```python
     TRANSCRIPT_KEYS = frozenset({"transcript", "turns", "text", "messages", "utterances", "dialog", "documentbody", "participants"})
     ...
     if nk in TRANSCRIPT_KEYS and isinstance(value, (str, list, tuple, Mapping)) and value:
         return _omitted(value)
     ```
   Verified on the live run: the audit `details.body.annotation.documentbody` currently keeps 200 base64 characters, which decode to the first customer message. This happens for successful pushes and for 403 attempts.
2. **`api/cqa/security/roles.py` (roles, audit quality).**
   - In the `ROUTE_MAP` rule for `POST /api/integrations/dynamics/transcripts`, put `"conversation=response:conversation_id"` before `"response:dataset_id"` in `entity`. Today every push is recorded as `dataset 1`, so filtering the audit by conversation misses D365 pushes.
   - Capture `turns`, `language`, `channel`, `job_id` and `evaluation.status` (if `capture` supports dotted names), in addition to `created`, `conversation_id` and `dataset_id`.
   - The handler separately checks `evaluations.run` for `evaluate: true`. An optional `refine=lambda b: "integration.dynamics.ingest_evaluate" if b.get("evaluate") else None` would make that visible in the audit.
3. **`api/cqa/ingest/normalize.py` (shared ingest, owner: integration step / ML).** In the chat branch of `normalize()`, fall back to ISO deltas when `parse_clock()` returns `None` (same code as the call branch, without the pause logic). Every ISO chat source would then get offsets and a first-response time. `enrich_conversation` then does nothing by itself.
4. **`api/cqa/models.py` + `api/cqa/serializers.py` (shared).** Add `Conversation.agent_name` (identity from the source system; for D365 it is in `meta.d365.agents[0]`) next to `operator_name`, which is detected from the greeting and fed to the judge. The operator portal can then match `X-CQA-User` against either.
5. **`api/cqa/config.py` (shared).** Add `web_base_url: str | None = None`, `public_api_url: str | None = None` and `integrations_api_key: str | None = None`. Values set in `.env` reach `settings`, not `os.environ`, and the router already falls back to `settings.<name>`. `CORS_ORIGINS` on a restricted instance must include `https://freedomholdingcorporation.crm11.dynamics.com` (web resource) and the web origin (IFRAME option).
6. **`docs/API.md` (shared).** Add a section:
   > ### Dynamics 365 integration
   > `POST /api/integrations/dynamics/transcripts` (permission `integrations.ingest`; `evaluations.run` with `evaluate`) body `{conversation_id?, language?, channel?, annotation?|documentbody?|transcript?|messages?, evaluate?, checklist_version_id?, provider?, model?, force?, overwrite?, audio_url?, private_messages?, control_messages?, order?, participants?}` → `{conversation_id, turns, language, job_id|null, card_url, embed_url, channel, created, operator_name, first_response_seconds, dataset_id, language_detected, speakers, d365, warnings, evaluation: {status: not_requested|started|skipped_unchanged, …}}`. Errors 400/401/403/404/409/413/422/429/503 as in `docs/INTEGRATION_D365.md` §3. `GET /api/integrations/dynamics/manifest` → web resource packaging (build command, `CQA_CONFIG` snippet, loader, form tab steps, Power Automate trigger/body, example payload).
7. **`web/` (frontend owner, optional).**
   - In `web/src/lib/config.ts`, also accept the D365 web resource parameters: `id` (strip `{}`, lowercase) as `conversation`, and `data` parsed as a query string for `api`/`conversation`/`embed`. Then `dist-single/index.html` works on a form tab without the snippet or the loader.
   - A Settings → Integrations panel could show `GET /api/integrations/dynamics/manifest` with copy buttons.
8. **`api/cqa/routers/integrations.py` + `api/cqa/judge/runner.py` (integration step).** `_members()` does not route `provider: "fake"` yet: any unknown name falls through to Anthropic. So the integrations router rejects `fake` (422 `unknown_provider`) rather than letting a push silently spend money. Once `_members()` routes `fake` (the testing package reports the same gap), add `"fake"` to `KNOWN_PROVIDERS` in `cqa/routers/integrations.py`.
