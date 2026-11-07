# Dynamics 365 integration: admin guide

How to connect CQA to the organizers' Dynamics 365 Contact Center (Omnichannel) at `https://freedomholdingcorporation.crm11.dynamics.com`:

1. **Transcripts in.** A Power Automate flow posts every closed conversation's transcript to `POST /api/integrations/dynamics/transcripts`. CQA stores it as a conversation and, optionally, evaluates it against the active checklist.
2. **Card out.** The evaluation card (the web app in embed mode, built as one HTML file) sits on a tab of the Conversation form, opened for the conversation being viewed.

`GET /api/integrations/dynamics/manifest` returns everything below as JSON, with the real API and web URLs filled in: the build command, the `window.CQA_CONFIG` snippet, the loader page, the form steps, the Power Automate trigger and HTTP body, and a ready-to-post example payload. Package internals: `docs/arch/integrations.md`.

Defaults: API `https://api-production-58ab4.up.railway.app`, web `https://cqa-freedom-pi.vercel.app` (environment variable `WEB_BASE_URL` on the API; `PUBLIC_API_URL` overrides the API URL that the manifest prints).

## 1. What we read from Dynamics 365

Sources: Microsoft Learn, [Download Dynamics 365 Contact Center transcripts in bulk](https://learn.microsoft.com/en-us/dynamics365/customer-service/develop/download-transcripts-bulk), the table references for [Transcript (msdyn_transcript)](https://learn.microsoft.com/en-us/dynamics365/developer/reference/entities/msdyn_transcript) and [Conversation (msdyn_ocliveworkitem)](https://learn.microsoft.com/en-us/dynamics365/developer/reference/entities/msdyn_ocliveworkitem), and the channel codes in [Work Stream (msdyn_liveworkstream)](https://learn.microsoft.com/en-us/dynamics365/customer-service/develop/reference/entities/msdyn_liveworkstream).

### Where the transcript lives

| Thing | Table | Fields we use |
|---|---|---|
| Conversation | `msdyn_ocliveworkitem` (entity set `msdyn_ocliveworkitems`, primary key `activityid`) | `activityid` (the id CQA uses), `statecode` (1 = Closed), `msdyn_channel` |
| Transcript row | `msdyn_transcript` | `msdyn_transcriptid`; lookup `msdyn_liveworkitemidid` → conversation (Web API: `_msdyn_liveworkitemidid_value`) |
| Transcript text | `annotation` (Notes) with `objecttypecode = 'msdyn_transcript'` | `documentbody` = the transcript, **base64-encoded**; `objectid` → the transcript row (Web API: `_objectid_value`); `annotationid` |
| Attachments | `annotation` with `objecttypecode = 'msdyn_ocliveworkitem'` | One extra note per file. A conversation with n files has n + 1 notes. CQA does not download them |

All transcripts: `GET [org]/api/data/v9.1/annotations?$filter=objecttypecode eq 'msdyn_transcript'`.

### The decoded `documentbody`

UTF-8 JSON: a list of messages. Every message carries `createdDateTime`. The fields we rely on:

| Field | Meaning | Example |
|---|---|---|
| `content` | Text. Control messages carry XML here | `"Здравствуйте!"`, `"<addmember>…<target>8:orgid:<agent id></target></addmember>"` |
| `contentType` | Usually `text`. HTML for rich-text agents | `"text"` |
| `createdDateTime`, `created` | ISO 8601 time of the message | `"2026-11-06T09:00:52.000+00:00"`, `"…Z"` |
| `id` | Epoch milliseconds | `"1793955652000"` |
| `from` | `null`, or an object with one non-null identity: `user`, `application`, `phone`, `guest`, … each `{displayName, id}` | `{"user": {"displayName": "Customer", "id": "…"}}` |
| `tags` | Comma-separated: `public` (agent to customer), `private` (agent to agent, hidden from the customer), `system` | `"public,messageId-…"` |
| `isControlMessage` | `true` for invisible events such as an agent joining or leaving | `true` |
| `attachments` | File metadata (`name`, `fileType`, `annotationid` of the file's note) | `[{"name": "screen.png", …}]` |
| `deliveryMode`, `deleted` | Bridged/unbridged delivery. A deleted message is dropped | `"bridged"` |

An anonymous customer shows as display name `Customer`; an identified one shows under their real name. System messages ("… has left the conversation.") come from the application identity `DynamicsBot` with tag `system`. Microsoft requires at least one customer message for a transcript to exist.

Things the documentation leaves open, and how we handle them:

- **Top-level shape.** Only individual messages are documented. We accept a list, any wrapper object (`messages`, `Content`, `value`, … including a JSON string inside) and JSON encoded twice.
- **Tags on customer messages.** One Microsoft sample shows a customer message tagged `private`. We never turn a customer's message into an internal note; `private` only marks an agent's message.
- **Order.** We keep the stored order. If every timestamp runs newest to oldest, we reverse the list.
- **Encoding.** UTF-8 is expected. A UTF-8 or UTF-16 BOM, UTF-16 without a BOM, gzip, URL-safe base64 and a `data:` URI prefix are also decoded.

## 2. Mapping to CQA

The result is the organizer record (`{conversation_id, channel, language, transcript: [{timestamp, speaker, text}], audio_url}`), stored by the same code as every other source (`cqa.ingest.service.upsert_record`).

| Omnichannel | CQA speaker | Text |
|---|---|---|
| Customer / end user (`Customer`, `Клиент`, `Visitor`, `Guest`…, tag `FromCustomer`, `phone`/`guest` identity) | `client` | as sent |
| Agent (`from.user` with a `public` tag, or the `8:orgid:<id>` target of an `<addmember>` event) | `operator` | as sent; HTML stripped |
| Bot (`from.application`, e.g. a Copilot Studio bot) | `bot` | as sent; adaptive-card text extracted |
| `DynamicsBot`, tag `system`, `from: null` | `system` | as sent |
| Control `<addmember>` / `<deletemember>` of an agent or the customer | `system` | `Агент присоединился к беседе`, `Агент вышел из беседы`, `Клиент вышел из беседы` (EN/KZ wording when the dialog is EN/KZ). Other control events are dropped (`control_messages: "all"` keeps them, `"none"` drops all) |
| Agent message tagged `private` (consultation between agents) | `system` | `[internal] <agent name>: <text>`. `private_messages: "skip"` drops them |
| Attachment | the sender | `[вложение: screen.png]` |

- An author without any signal counts as an agent. A transcript always has a customer, so if no author was recognised as one, the first author to speak becomes the client. The `participants` map (`{"display name or id": "client|operator|bot|system"}`) overrides all of this.
- If every agent message is tagged `private`, the tag cannot mean "between agents", so those messages stay operator turns. The response then carries a warning.
- **Timestamps** are kept as sent (ISO). Chat offsets, the first-response time and duplicate detection are computed from them.
- **Language**: `language` from the request (`RU`, `KZ`, `EN`, `ru-RU`, `kk-KZ`, LCID 1049/1087/1033), otherwise detected from the customer's and the agent's text. Kazakh-specific letters `әғқңөұүһі` (at least 2 % of the Cyrillic letters) give `KZ`, other Cyrillic gives `RU`, anything else `EN`.
- **Channel**: `agent chat` unless the request says `channel: "voice"`, or `msdyn_channel` / `msdyn_streamsource` is `192370000` (Voice) or `192440000` (Voice call), or the transcript targets a `phonecall`, or `audio_url` is given.
- **Conversation id**: `conversation_id` from the request, else `msdyn_ocliveworkitemid`, `_msdyn_liveworkitemidid_value`, `liveworkitemid`, the conversation's `activityid`, then the transcript id. As a last resort, a hash of the content (`d365-…`). GUIDs are stored lowercase without braces, which is what the form script sends.
- The operator name comes from the agent's greeting ("Меня зовут Алина"), the same way as for organizer data. The judge sees it as "detected", so the Dynamics display name is not put there. Agent display names are kept in `meta.d365.agents`; customer display names are never stored.

## 3. Push endpoint

`POST /api/integrations/dynamics/transcripts` (permission `integrations.ingest`: roles `analyst`, `lead`, `admin`, or no `X-CQA-Role` header). With `evaluate: true` it also needs `evaluations.run`.

```json
{
  "conversation_id": "7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
  "annotation": {
    "annotationid": "3a5c7e90-0000-4000-8000-00000000a365",
    "objecttypecode": "msdyn_transcript",
    "_objectid_value": "9b1d3f50-0000-4000-8000-00000000f365",
    "documentbody": "W3siaWQiOiAiMTc5Mzk1NTYwMDAwMCIsICJjb250ZW50IjogItCX0LTRgNCw…"
  },
  "msdyn_channel": "192360000",
  "evaluate": true
}
```

Give exactly one transcript source: `annotation` (the note), `documentbody`, `transcript` (decoded JSON) or `messages` (a plain list; simplified items `{speaker|role, text|content, timestamp}` are accepted). Optional fields:

| Field | Meaning |
|---|---|
| `language`, `channel` | Override detection |
| `checklist_version_id` | Default: the active version |
| `provider`, `model` | `anthropic`, `local`, `ensemble`, or `anthropic:<model>`. Default: the provider set in Settings |
| `force` | Re-evaluate even when the same content was already evaluated |
| `overwrite` | Replace a conversation with the same id that came from another source |
| `audio_url` | Call recording link (http/https) |
| `private_messages` | `system` (default) or `skip` |
| `control_messages` | `events` (default), `all` or `none` |
| `order` | `auto` (default), `keep` or `chronological` |
| `participants` | Display name or id mapped to a role |

Any other Dataverse column may be sent as is (`msdyn_ocliveworkitemid`, `msdyn_channel`, …).

Response (`200`):

```json
{
  "conversation_id": "7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
  "turns": 7, "language": "RU", "job_id": "j_3fa94c0b12de",
  "card_url": "https://cqa-freedom-pi.vercel.app/#/conversations/7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
  "embed_url": "https://cqa-freedom-pi.vercel.app/?embed=1&conversation=7f0c2a4e-1b3d-4c5e-9f60-00000000d365",
  "channel": "agent chat", "created": true, "operator_name": "Алина", "first_response_seconds": 22.0, "dataset_id": 3,
  "language_detected": true, "speakers": {"client": 2, "bot": 1, "system": 2, "operator": 2},
  "evaluation": {"status": "started", "job_id": "j_3fa94c0b12de", "checklist_version_id": 1, "provider": "anthropic",
                 "model": null, "estimated_cost_usd": 0.18},
  "d365": {"transcript_id": "9b1d3f50-…", "annotation_id": "3a5c7e90-…", "conversation_id_source": "conversation_id", "order": "kept", "…": "…"},
  "warnings": []
}
```

Re-posting is safe: the conversation is replaced in place (an upsert by id). A transcript that comes back unchanged is not evaluated again while an evaluation of it is queued, running or completed for the same checklist version (`evaluation.status: "skipped_unchanged"`). A changed transcript, another checklist version or `force: true` evaluates again. All pushed conversations go into one dataset, `D365` (filter the conversation list by it; `GET /api/export/results.json?group_id=D365` exports their results).

Errors use the usual envelope `{"error": {"code", "message", …}}` with Russian messages:

| Status | Code | When |
|---|---|---|
| 400 | `invalid_json`, `empty_body` | The body is not JSON |
| 400 | `no_checklist` | `evaluate` with no checklist in the system |
| 401 | `integration_key_required`, `invalid_integration_key` | `INTEGRATIONS_API_KEY` is set and the header is missing or wrong |
| 403 | `forbidden` | The role lacks `integrations.ingest` (or `evaluations.run` with `evaluate`) |
| 404 | `checklist_version_not_found` | Unknown `checklist_version_id` |
| 409 | `conversation_exists` | Same id already loaded from another source; send `overwrite: true` |
| 409 | `concurrent_update` | Two pushes of the same conversation collided; send again |
| 413 | `payload_too_large` | Body over 8 MB, `documentbody` over 20 M characters, or more than 5000 messages |
| 422 | `invalid_body` | The JSON body is not an object |
| 422 | `validation_error` (with `errors[]`) | Wrong field types |
| 422 | `missing_transcript`, `invalid_base64`, `invalid_transcript_json` | No usable transcript in the request |
| 422 | `not_a_transcript` | An attachment note was sent instead of the transcript note |
| 422 | `empty_transcript` | No customer, agent or bot messages |
| 422 | `invalid_conversation_id`, `invalid_language`, `invalid_channel`, `unknown_provider`, `invalid_audio_url`, `invalid_participants` | A bad value in the named field |
| 429 | `budget_exceeded`, `too_many_jobs` (header `Retry-After`) | The platform guards (daily budget, concurrent jobs). The transcript **is stored** (`ingested: true`); evaluate it later from the UI |
| 503 | `evaluation_not_started` | Stored, but the job could not start |

## 4. Power Automate flow

The flow sends each transcript when the conversation closes. The HTTP action is a premium connector.

1. **Trigger**: *Microsoft Dataverse → When a row is added, modified or deleted*. Change type: *Added or Modified*; table: *Notes*; scope: *Organization*; filter rows: `objecttypecode eq 'msdyn_transcript'`. If the trigger output lacks `documentbody`, add *Get a row by ID* on Notes and select `documentbody`.
2. **Get_transcript**: *Get a row by ID*. Table *Transcripts*; row ID `@{triggerOutputs()?['body/_objectid_value']}`; select columns `_msdyn_liveworkitemidid_value`.
3. **Get_conversation**: *Get a row by ID*. Table *Conversations*; row ID `@{outputs('Get_transcript')?['body/_msdyn_liveworkitemidid_value']}`; select columns `statecode,msdyn_channel`.
4. **HTTP**: method `POST`, URI `https://api-production-58ab4.up.railway.app/api/integrations/dynamics/transcripts`. Headers: `Content-Type: application/json`, `X-CQA-Role: analyst`, `X-CQA-User: Power%20Automate` (and `X-CQA-Integration-Key` when the key is configured). Body:

```json
{
  "conversation_id": "@{outputs('Get_transcript')?['body/_msdyn_liveworkitemidid_value']}",
  "msdyn_channel": "@{outputs('Get_conversation')?['body/msdyn_channel']}",
  "annotation": {
    "annotationid": "@{triggerOutputs()?['body/annotationid']}",
    "objecttypecode": "@{triggerOutputs()?['body/objecttypecode']}",
    "_objectid_value": "@{triggerOutputs()?['body/_objectid_value']}",
    "documentbody": "@{triggerOutputs()?['body/documentbody']}"
  },
  "evaluate": "@{equals(outputs('Get_conversation')?['body/statecode'], 1)}"
}
```

5. Optional: *Parse JSON* on the response, then *Update a row* to write `card_url` into a custom column on the conversation.

Notes:

- `evaluate` is true only for closed conversations, so a transcript saved mid-conversation is stored without spending money. A string `"true"` / `"false"` is accepted.
- The HTTP action's default retry policy (408/429/5xx) is safe: re-posts are upserts and an unchanged transcript is not evaluated twice.
- Test without Dynamics 365: `curl -X POST <API>/api/integrations/dynamics/transcripts -H 'Content-Type: application/json' -d @payload.json`, where `payload.json` is `power_automate.example_payload` from the manifest (a synthetic chat, `evaluate: false`).

## 5. Evaluation card on the Conversation form

A web page (HTML) web resource on a form only receives the parameters Dynamics 365 allows (`id`, `typename`, `type`, `orgname`, `userlcid`, `orglcid`, `data`, …). Others make the request fail ([HTML web resources](https://learn.microsoft.com/en-us/power-apps/developer/model-driven-apps/webpage-html-web-resources)). The card reads `embed`, `conversation` and `api` from the query string, the URL hash or `window.CQA_CONFIG`, so it needs one of the two adapters below.

**A. One web resource (recommended)**

1. Build the single file. `cd web && pnpm install && pnpm build:single` → `web/dist-single/index.html` (about 1.7 MB, everything inlined, hash routing, no cookies).
2. Paste this snippet into `<head>` of `dist-single/index.html`, before the first `<script>` (the manifest's `web_resource.config_snippet` has your API URL):

```html
<script>
  /* CQA: Dynamics 365 passes ?id={record GUID}&data=<custom parameter>; the card reads window.CQA_CONFIG. */
  (function () {
    var q = new URLSearchParams(window.location.search);
    var data = new URLSearchParams(q.get("data") || "");
    var id = (data.get("conversation") || q.get("conversation") || q.get("id") || "").replace(/[{}]/g, "").toLowerCase();
    window.CQA_CONFIG = {
      apiUrl: data.get("api") || "https://api-production-58ab4.up.railway.app",
      embed: true,
      conversation: id || undefined
    };
  })();
</script>
```

3. In make.powerapps.com, open your solution, choose New → More → Web resource. Type *Webpage (HTML)*, name `cqa_/card/index.html` (use your publisher prefix), upload the file, save and publish. The upload limit is the organization's attachment size limit (`Organization.MaxUploadFileSize`, see [web resources](https://learn.microsoft.com/en-us/power-apps/developer/model-driven-apps/use-web-resources)). Raise it if the 1.7 MB file is refused.
4. Open the main form of the **Conversation** table (`msdyn_ocliveworkitem`) and add a tab *Оценка качества* with an *HTML web resource* component that points to `cqa_/card/index.html`.
5. In the component's properties, turn on **Pass record object-type code and unique identifier as parameters** (adds `?id={GUID}`), set **Custom parameter (data)** to `api=https://api-production-58ab4.up.railway.app`, and stretch it to the full tab (for example 30 rows). Save and publish the form.
6. Open a closed conversation whose transcript the flow has already sent: the tab shows its card (score ring, criteria, evidence, history, trace). Appeals are hidden in embed mode.

**B. Keep the build untouched.** Upload `dist-single/index.html` as is, plus a small loader `cqa_/card/loader.html` next to it (manifest: `web_resource.loader_html`). Point the form component at the loader. It moves `id`/`data` into the URL hash, which Dynamics 365 does not validate:

```html
<!doctype html>
<html><head><meta charset="utf-8"><title>CQA</title></head><body>
<script>
  (function () {
    var q = new URLSearchParams(window.location.search);
    var data = new URLSearchParams(q.get("data") || "");
    var id = (data.get("conversation") || q.get("id") || "").replace(/[{}]/g, "").toLowerCase();
    var api = data.get("api") || "https://api-production-58ab4.up.railway.app";
    window.location.replace("index.html#?embed=1&conversation=" + encodeURIComponent(id) + "&api=" + encodeURIComponent(api));
  })();
</script>
</body></html>
```

**C. IFRAME to the hosted web app.** Add an IFRAME control `IFRAME_cqa_card` to the tab and a JavaScript web resource with this OnLoad handler (register `cqaConversationOnLoad` and pass the execution context):

```js
function cqaConversationOnLoad(executionContext) {
  var formContext = executionContext.getFormContext();
  var id = (formContext.data.entity.getId() || "").replace(/[{}]/g, "").toLowerCase();
  var frame = formContext.getControl("IFRAME_cqa_card");
  if (frame && id) {
    frame.setSrc("https://cqa-freedom-pi.vercel.app/?embed=1&conversation=" + encodeURIComponent(id));
  }
}
```

## 6. CSP and CORS

- **CORS.** With A or B the card runs on the organization's domain and calls the API from `https://freedomholdingcorporation.crm11.dynamics.com`. With C it calls from `https://cqa-freedom-pi.vercel.app`. The API's default `CORS_ORIGINS` is `["*"]`. If it is restricted, add the origin you use, for example `CORS_ORIGINS='["https://cqa-freedom-pi.vercel.app","https://freedomholdingcorporation.crm11.dynamics.com"]'`. Preflight is answered for the custom headers (`X-CQA-Role`, `X-CQA-User`, `X-CQA-Integration-Key`). Power Automate calls are server-to-server and need no CORS.
- **CSP** ([Content security policy](https://learn.microsoft.com/en-us/power-platform/admin/content-security-policy), Power Platform admin center → Environments → Settings → Product → Privacy + Security). When enforcement is on, the default model-driven policy allows inline scripts and does not restrict `connect-src` or `frame-src`, so A, B and C work as is. With **Strict CSP**:
  - add the API origin to `connect-src`;
  - allow the build's inline script in `script-src` (A and B; the single-file build inlines its JavaScript);
  - for C, add the web origin to `frame-src`.
  `frame-ancestors` concerns embedding Dynamics 365 elsewhere and needs no change.

## 7. Security and personal data

- **Access.** With the shared secret set (`INTEGRATIONS_API_KEY` on the API), every push must carry `X-CQA-Integration-Key`, compared in constant time. Without it the endpoint is as open as the rest of the demo API, guarded by roles and the platform budget. Set the key on any instance that holds real data, and keep it in a Power Automate secure input or environment variable. The manifest only says whether a key is required and never shows it.
- **Roles.** Send `X-CQA-Role: analyst` (or `lead`/`admin`) and a recognisable `X-CQA-User`. Every push is then attributed in the audit trail as action `integration.dynamics.ingest`. Operators, reviewers, directors and visitors get 403.
- **Personal data.** Transcripts contain it. Before text goes to a cloud model, phone numbers, ИИН, card numbers, IBAN and e-mails are masked (`pii_masking`). The local provider receives text unmasked; use it when data must not leave the network. Names stay in the text, because criterion 1 checks the operator's introduction. Customer display names from `from.user.displayName` are not stored. Agent names are kept as attribution. Attachments are never downloaded. The audit trail drops `messages` and `transcript` bodies, but until the open integration request in `docs/arch/integrations.md` is applied it still keeps the first 200 characters of `documentbody`, which decode to the start of the transcript. Treat the audit log as personal data until then.
- **Volume and cost.** Bodies over 8 MB or 5000 messages are refused. Evaluation obeys the daily budget (token-based estimate per dialog; a transcript over 100 000 characters or 2 000 turns is kept but not evaluated, 422 `transcript_too_long`) and the concurrent-job limit. An unchanged transcript is never paid for twice.

## 8. Limitations

- Voice transcripts kept in the file columns of `msdyn_transcript` (`msdyn_voicetranscript`, `msdyn_voicetranscript_formatted`) are not read. For calls, send `messages` (speaker, text, ISO timestamp), `channel: "voice"` and the recording link in `audio_url`.
- Attachments are listed by file name only.
- The card shows a conversation only after its transcript has reached the API. A conversation that is still open shows "not found" until the flow posts it.
- Speaker roles are inferred from the documented fields. If a deployment labels customers in a way not covered here, send `participants`.
- The embedded card is read-only. Appeals are filed in the full web app (`card_url`).

## 9. Troubleshooting

| Symptom | Check |
|---|---|
| Tab shows "Укажите ?conversation=<id>" | The CQA_CONFIG snippet or the loader is missing, or the *Pass record … as parameters* option is off |
| Card says the conversation is not found | The flow has not posted it yet, or it was posted under another id (the response's `d365.conversation_id_source` and `warnings` explain which id was used) |
| `not_a_transcript` | The flow picked an attachment note: filter the trigger on `objecttypecode eq 'msdyn_transcript'` |
| Customer lines appear as operator | Send `participants: {"<customer display name>": "client"}`, or check the tags the deployment uses |
| `429 budget_exceeded` | The daily budget is spent. The transcript is stored; evaluate later from the UI or raise `DAILY_BUDGET_USD` |
