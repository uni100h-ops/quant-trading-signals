# QTS publisher guide — Antonio

This document is for the developer. Customers use README.md.

## Your revenue and release policy

The 0.01% Builder Fee is YOUR developer commission, received by your public Hyperliquid builder address. It is additional to Hyperliquid's normal exchange fee. It is attached to filled entries and additions; protective exits and stops do not wait for payment authorization. x402 service revenue is a separate 0.01 USDC payment to your public Algorand recipient. Neither payment is an Algorand Foundation charge.

The public recipients, developer rate, service price, service URL and approved network sponsors now live in `PUBLISHER_POLICY` inside `agente.py`, not in customer-editable `config.txt`. Conflicting legacy configuration fields are rejected. These addresses are PUBLIC; do not put recipient private keys in the release.

The distributed template contains no invented payout addresses. Live mode remains blocked until you embed your own addresses and deployed HTTPS endpoint. Keep the current rates unless you deliberately update the code, customer fee table and consent together.

1. Install dependencies and run `python agente.py --preparar-publicacion`.
2. Enter your own public Hyperliquid builder address, Algorand USDC recipient and paid API HTTPS URL. The wizard reads the facilitator's advertised network fee sponsors and shows the full public policy. Type `EMBED` to write it into the release source.
3. Restart Python after embedding. Your Hyperliquid builder account must meet the documented conditions: at least 100 USDC in perps account value and standard account mode. Your Algorand recipient must have USDC enabled on the chosen network.
4. Deploy the SAME embedded source using `python agente.py --servir`, with `estado_servidor/` persistent and an HTTPS reverse proxy. `Dockerfile` is supplied; use `[server] host = 0.0.0.0` in a container. Run one instance unless you replace SQLite with appropriate shared transaction locking.
5. Check terms, configured sponsor support, customer approval, a funded Testnet payment, and an entry/stop flow. Confirm both fee recipients and actual amounts before Mainnet distribution. No funded network tests were performed in this delivery.
6. Distribute with mode `paper`, empty customer addresses, `completed = no` and empty `acceptance_sha256`. Keep your provider policy embedded. Customers complete setup themselves.

## USDC-only recurring payments

The customer signs a USDC ASA transfer with fee **zero**. A separate facilitator transaction covers the atomic group's ALGO network fees. Both client and API validate the pinned sponsor, recipient, amount, network, group hash, transaction types and restricted permissions. The customer never signs the sponsor's transaction. No USDC-to-ALGO swap is performed, and there is no hidden extra debit to the customer's wallet.

The standard two-transaction group needs 0.002 ALGO at the ordinary minimum fee. This version allows the sponsor fee up to 0.004 ALGO and otherwise pauses new paid requests. The service fee stays 0.01 USDC; the sponsor must actually support and fund this arrangement. Do not assume a public facilitator's sponsorship is free or permanently available: confirm its terms and verify funded settlement. The paid API refuses to start if its embedded sponsor is not advertised for the selected network.

**Sponsorship of transaction fees does not activate an empty wallet.** A standard account needs 0.1 ALGO minimum balance, plus 0.1 ALGO for its USDC holding, before considering other resources. The provider must arrange that reserve and the signed USDC opt-in before asking a new customer to deposit USDC. This package detects a missing activation and stops setup; it does NOT implement or claim an automatic reserve-funding service. The wizard no longer prompts the customer to buy ALGO for recurring payments.

A swap cannot solve initial activation by itself: receiving USDC requires opt-in, and the swap also needs network fees. To promise “deposit only USDC” from a completely new wallet, first operate and fund an onboarding sponsorship service. Never distribute a common payment-wallet private key to customers.

Official references: [Algorand fee pooling](https://dev.algorand.co/concepts/transactions/fees/), [account minimum balance](https://dev.algorand.co/concepts/accounts/overview/), [x402 AVM specification](https://github.com/GoPlausible/x402).

## Can a customer remove the fee?

**Yes, a customer can modify software running on their own computer.** Embedding an address removes a normal setting; it does not make the fee impossible to bypass. Packaging Python as an executable also cannot guarantee this. Do not advertise tamper-proof commissions.

The remote API enforces payment before delivering its own risk checks and receipts. However, the trading rules are still local in this release, so a modified client could bypass that service. Stronger business protection requires keeping valuable signal calculation or another indispensable service on your backend and selling access. That would change the architecture and has NOT been implemented here. Users always retain wallet control and can revoke builder authorization.

## What to upload to GitHub

Upload the contents of `QUANT_TRADING_SIGNALS_GitHub.zip`, preserving folders. Publish the Windows dependency ZIP as a GitHub Release asset. The package contains Python source and a guided installer, not a signed Windows executable. Do not upload customer keys, `.venv`, `estado/`, `estado_servidor/`, personal logs or configured customer accounts.

The MP4 is included at `media/QUANT_TRADING_SIGNALS_Demo.mp4`. Open it locally or use `media/WATCH_VIDEO.html`. Upload it to a video host or GitHub Release to obtain a public contest URL; no URL is invented. A relative `media/...` link shown in ChatGPT may incorrectly resolve to chatgpt.com and show 404, which is why the customer README gives the local path instead.

## Backtest and verification

The highlighted +238.57% is the original annual selected-strategy simulation, before the new developer and service charges. `docs/BACKTEST_REFERENCE.json` preserves its original metrics and provenance. Do not present it as a fresh sponsored-payment strategy backtest or live performance. This revision changes billing and documentation, not entry or stop rules.

Network-dependent production validation remains pending. Local tests use genuine SDK transactions and signatures with simulated settlement, never real funds. Native Windows installation and actual wallet-extension signing still require checks on the target computer.

For existing installations, resolve pending payments with the previous release before switching payment policy. New terms require setup and consent again. Do not delete the SQLite journals to bypass this check.
