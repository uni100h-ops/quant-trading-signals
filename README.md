Server side of QUANT TRADING SIGNALS. Render deploys from this repository.

This repository must stay private. estrategia.py is the paid product: it holds the sweep/context layer, the entry rule and every tuned parameter. If it becomes public, the product is given away — a customer's agent could then decide entries locally and skip both fees.

Files
File	Why it's here
estrategia.py	The private entry engine. Never distributed, never committed to the public repo.
agente.py	Same file customers get. --servir runs the API; it imports estrategia.py.
config.txt	Server settings: facilitator URL, host, port. Required by the Dockerfile.
requirements.lock	Pinned dependencies with hashes.
Dockerfile	Render build. Runs python agente.py --servir.

Keep agente.py, config.txt and requirements.lock in sync with the public repo. Only estrategia.py is exclusive to this one.

Before first deploy
Embed the publisher policy. On a local copy, run python agente.py --preparar-publicacion and enter the Render HTTPS URL, the Hyperliquid builder address and the Algorand payout address. This rewrites PUBLISHER_POLICY inside agente.py. Commit that result here and in the public repo — they must be byte-identical, or the customer's setup wizard rejects the service terms.
Confirm config.txt is present here. The Dockerfile copies it; the build fails without it. Use [server] host = 0.0.0.0 for a container.
Fund the builder account so its Hyperliquid perpetual account value meets the 100 USDC minimum, in standard account mode. Confirm the payout wallet's USDC opt-in.
Deploy on Render
Point the service at this repository. Dockerfile is the runtime; the port is read from Render's environment.
No API key, private key or customer credential belongs in this repo or in Render's environment. The service receives no keys and cannot execute trades.
After deploy, check /health and /v1/terms. /v1/terms must show version 2.2.0 and builder_scope: exits_only. If it still shows 2.1.0, the deploy didn't apply.
--servir refuses to start if estrategia.py is missing or the publisher policy is blank. That's deliberate.
Free tier trade-off

Render Free sleeps after 15 idle minutes and cold-starts. Entries now depend on this service, so a cold start can miss a signal's freshness window (60 s) and skip a valid entry. It cannot leave a position unprotected — stop management runs on the customer's machine. For a pilot, Free is fine; with paying customers, a plan that doesn't sleep is worth it.

Local files are lost on restart or redeploy, so the payment journal in estado_servidor/ does not survive. Before charging real customers, move it to a persistent disk or an external store.

Endpoints
Endpoint	Paid?	What it does
GET /health	no	Liveness.
GET /v1/terms	no	Published fee terms. Must match the customer's embedded policy.
POST /v1/signal/BTC	activation required	The entry decision. Returns signal (-1/0/+1) and risk_atr.
POST /v1/audit	x402	Activation and closed-position receipts.

/v1/signal/BTC requires a signed request from an activated payer and rejects stale timestamps (over 60 s). It charges nothing per call — the x402 payments happen at activation and on close.

Security checklist
2FA on the GitHub and Render accounts. Whoever gets in there gets the strategy.
git status before every push to the public repo: estrategia.py is in its .gitignore, but check anyway.
An activated customer can poll /v1/signal/BTC candle by candle and log the answers. Enough history could let someone approximate the entry timing. That's inherent to selling signals and no hosting choice fixes it.
Keeping the strategy intact

estrategia.py was verified to produce identical output to the original monolithic version — signal and risk_atr bit-for-bit, across a sample containing real entries. Two things to respect when editing:

The entry signal is the filtered EMA cross. The sweep/context layer contributes only its ATR, used as risk_atr. Do not turn it into an AND of both layers — that silently changes the strategy the backtest was run against.
Indicator periods in agente.py (StopIndicators: EMA 34/89, RSI 14, ATR 7) must match the ones here. They stay client-side because trailing stops need them.
Related
Public repo: https://github.com/uni100h-ops/quant-trading-signals
Existing x402 competition endpoint (separate, unaffected): https://github.com/uni100h-ops/x402-quant-signals
