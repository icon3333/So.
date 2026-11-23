# Inoreader item ingestion contract

This repository's private n8n workflow cannot be edited or verified here. OAuth still authenticates to that workflow; existing `read` and `remove` inventory operations are unchanged. An old workflow that supports only inventory will reject `operation: "entries"`; setup reports the workflow as outdated, does not report a successful import, and leaves existing catalog/review entries untouched.

## Private n8n route contract

The local adapter calls the existing authenticated HTTPS webhook with JSON:

```json
{"operation":"entries","cursor":null,"limit":100}
```

Subsequent calls pass the opaque `nextCursor` unchanged. The route must perform **read-only** retrieval from Inoreader and return:

```json
{"complete":true,"entries":[{"id":"<stable Inoreader item id>","url":"https://...","title":"...","content":"...","excerpt":"...","author":"...","publisher":{"title":"...","url":"https://..."},"published":"..."}],"nextCursor":null}
```

`entries` is one page of at most the requested limit; `nextCursor` is a nonempty opaque string when another page exists and `null` at the end. `complete:true` means that page was fetched and parsed successfully, not that the entire multi-page scan ended. Any failure must return an error/non-success response, never an empty successful page. The adapter caps at five pages, 100 per page, and 500 total entries per run. It validates item identifiers and HTTP(S) URLs. Local persistence merges by stable `inoreader:<item-id>` key; repeated imports update matching metadata, preserve other local entries and do not delete absent entries. It makes no remote writes or removal requests.

## Official Inoreader API reference

The canonical official endpoint for reading stream items is `GET https://www.inoreader.com/reader/api/0/stream/contents/{streamId}` (OAuth bearer token; `n=...` controls page size and `c=...` supplies continuation). For Read Later/starred content, the stream ID is `user/-/state/com.google/starred`. The route should use the official API's continuation token to implement this adapter's opaque cursor and map item IDs, canonical URLs, title, content, author, origin/publisher, and published timestamp into the shape above. Consult current endpoint/authentication details at https://www.inoreader.com/developers/ before deploying; no live API or private workflow configuration was exercised for this change.

Local catalog file: `inoreader-entries.json` (schema version 1), separate from the original inventory snapshot and review-state file.
