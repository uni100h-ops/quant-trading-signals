# QUANT TRADING SIGNALS

## Project one-liner

QUANT TRADING SIGNALS is a downloadable Python agent for BTC/USDC trading on Hyperliquid, with entries confirmed by a paid publisher service and usage-based payments settled on Algorand via x402.

## Project Description

QUANT TRADING SIGNALS is a local Python agent that users download or clone from GitHub. A guided installer prepares its dependencies, validates the required credentials and writes one clear configuration file. Trading and payment keys are stored in the operating system credential store, while the main Hyperliquid wallet seed is never requested.

The agent trades BTC/USDC perpetuals on Hyperliquid using closed five-minute candles, EMA crosses, ATR, RSI and published funding, with an adaptive stop that tightens as trend conditions weaken and a maximum of three entries under isolated margin. Every candidate entry is confirmed against the publisher's own server-side computation of the same signal before the agent is allowed to open a position, so the client is never the sole authority on what counts as a valid entry. Timestamped console and file logs explain the agent's actions, and persistent state supports recovery without replaying old signals.

The implementation includes an explicitly approved developer commission and x402 service payments, both billed only when a position closes — opening a trade is free, and a protective stop or exit is never held up waiting on payment. The Hyperliquid Builder Fee (0.10% of the closing fill) goes to the developer; a 0.01 USDC Algorand x402 payment covers one-time activation and a receipt for each closed position. Recurring payment network fees are sponsored using atomic transaction groups; the customer signs only a zero-fee USDC transfer. A new payment wallet still needs provider activation and USDC opt-in. Payment limits and durable transaction records prevent blind retries and duplicate charges.

This is an agent-first product, not a trading website. Both payment integrations are implemented and locally tested; embedded publisher payout addresses and verified fee sponsors, a deployed HTTPS endpoint and funded network verification are still required before customer production use. Historical strategy results do not establish the performance of this fee-bearing version, and client-reported closing receipts are not independent proof of exchange execution.

## Demo video

`media/QUANT_TRADING_SIGNALS_Demo.mp4` — 96-second customer walkthrough in English, with on-screen captions and no audio. It shows GitHub distribution, mandatory setup, wallet setup, explicit fee prices, key storage, English logs and the previous annual backtest with its limitations. Credentials and transactions shown are illustrative test data; no mainnet order or payment is presented as executed.

Upload the MP4 to the repository's GitHub Release or your video channel, then use that resulting public URL in the submission. No repository URL, project profile or uploaded-video URL is invented in this package.

## GitHub repository description

Local BTC/USDC Python trading agent with guided setup, adaptive stops, secure credentials, server-confirmed entries, Hyperliquid Builder Fees and Algorand x402 close-only payments.
