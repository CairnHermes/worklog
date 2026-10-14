# VERIFY - whatsapp-messenger-reply-automation

What was built, how it was verified, and the honest limits.

## Built (PoC.py)

A single service, both channels, one code path:

1. `parse_inbound` - normalize the WhatsApp Cloud envelope AND the Messenger
   Graph envelope into one shape `{platform, sender, text, direction,
   message_id}`. Accepts a raw JSON string (webhook body) or a dict.
   Non-text events (image, postback, opt-in) correctly return `None`.
2. `compute_signature` / `verify_signature` - X-Hub-Signature-256 (HMAC-SHA256,
   timing-safe `hmac.compare_digest`), identical for both channels.
3. `route_reply` - case/punctuation-insensitive keyword router with a
   guaranteed fallback (a customer is never left without a reply).
4. `build_outbound` - the correct signed Graph send payload per platform
   (WhatsApp `/v18.0/<wamid>/messages`, Messenger `/v21.0/<uid>/messages`).
5. `send` - POST via `requests` (real) or dry-run (print). Dry-run by default.
6. `WebhookHandler` + `build_server` - a stdlib-only HTTP receiver that does
   GET hub-verify handshake + POST verify-sign / parse / route / send.

## Verified this run

### selftest (offline, no network)
```
$ python PoC.py selftest
selftest: 38/38 checks passed
all checks passed
```
exit 0. Covers: WhatsApp inbound parse (5 fields), WhatsApp image→None,
Messenger inbound parse (5 fields), Messenger echo/postback→None, junk→None,
route cost/schedule/quote/price/support, fallback, never-None, signature
format + good + bad + empty + wrong-token, WhatsApp outbound url/body/auth,
Messenger outbound url/body, unknown-platform→ValueError, dry-run flag,
full pipeline (parse→route→outbound) for both platforms.

### demo (dict path)
```
$ python PoC.py demo
```
WhatsApp price/schedule, Messenger support, WhatsApp greeting-fallback: all
parse, route to the right reply, build the correct outbound URL
(`/v18.0/...` and `/v21.0/...`), and dry-run send. exit 0.

### live HTTP (real network round-trip, local server on 127.0.0.1:8765)
```
$ python PoC.py serve --host 127.0.0.1 --port 8765 --token <t>   (background)
```
- **TEST 1** WhatsApp inbound, valid X-Hub-Signature-256, "schedule" keyword
  -> `200` `handled:true platform:whatsapp reply:"I am available to start
  this week..." send.dry_run:true url:.../v18.0/1/messages`. PASS
- **TEST 2** same body, bad signature -> `HTTP 401`. PASS (forged/spam
  rejected)
- **TEST 3** `GET /?hub.mode=subscribe&hub.verify_token=<t>&hub.challenge=8888`
  -> body `8888`. PASS (Meta verification handshake)
- **TEST 4** Messenger inbound, valid signature, "support" keyword
  -> `200` `handled:true platform:messenger reply:"Yes, I include one week of
  post-delivery support..." url:.../v21.0/UID-LIVE-7/messages`. PASS

All four live HTTP tests passed against a real local server over real
connections.

## Honest limits (not hidden)
- Outbound send is **dry-run by default** and code-reviewed against the real
  Graph endpoints. A live send requires the client's own access token +
  phone number id / PSID, which I do not have. The routing, parsing, and
  signature-verification logic is fully exercised with live HTTP.
- This box has no Excel COM server (irrelevant here) and does not set up the
  client's Meta Business account, WhatsApp Business onboarding, or Messenger
  app. Those are client-side prerequisites flagged in the bid.
- No scraping, no bot-wall evasion, no ToS-violating automation: both
  channels are Meta's official, documented APIs and we only answer a message
  the customer actually sent.

## Files
- `PoC.py` - the working, runnable service (selftest / demo / serve / send).
- `BID.md` - the human bid + why it is bid-able + honest limits.
- `VERIFY.md` - this file.
