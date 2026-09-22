# QUANT TRADING SIGNALS (QTS)

**Automated BTC/USDC trading on Hyperliquid — runs on your own computer, pays per use on Algorand.**

QTS is a local Python agent. It watches the market, opens qualifying trades and manages a dynamic stop as conditions change. You keep custody of your wallets and can stop the agent at any time.

Entry signals are computed by the publisher's service and paid for with Algorand x402 micropayments. Protecting an open position — stop placement, trailing, emergency exit — runs locally, so a network or service outage can never leave a funded position unmanaged.

## Download

**[Download the Windows installer](../../releases/latest)** — one ZIP with everything bundled, including dependencies.

Prefer to clone? `git clone` this repository and run `instalar.sh` (macOS/Linux) or `INSTALAR.bat` (Windows). Cloning installs dependencies from PyPI instead of the bundled wheels, so you'll need an internet connection during setup.

## Before you start

- Windows 10/11, macOS, or Linux. Python 3.12 is installed for you on Windows.
- A Hyperliquid account with **at least 100 USDC** in futures equity, standard account mode.
- A secondary Algorand wallet, already activated (0.2 ALGO reserve + USDC opt-in), funded with **at least 2 USDC**.
- An internet connection while the agent runs.

## Install & run (Windows)

1. **Extract the ZIP** into its own folder — don't run it from inside the ZIP.
2. Double-click **`INSTALAR.bat`**. It sets up a local environment and walks you through entering your Hyperliquid public address, a **separate API wallet key**, and your Algorand payment wallet key. **Never enter your main wallet's seed phrase — anywhere.**
3. Review the fee terms it shows you and approve them in your wallet when asked.
4. When you see **SETUP COMPLETE**, double-click **`INICIAR.bat`** and keep that window open — that's the agent running.

**New to QTS? Run `PROBAR_PAPER.bat` first.** It runs the agent's plumbing with no keys and no real payments, so you can see how it behaves before risking anything.

**macOS / Linux:** install Python 3.12, then `./instalar.sh` followed by `./iniciar.sh`.

## Your wallets

| Wallet | What it's for | Minimum |
|---|---|---|
| Hyperliquid trading wallet | Holds your trading capital (USDC) | 100 USDC in futures equity |
| Hyperliquid API wallet | Separate key QTS uses to sign orders — create it at the [Hyperliquid API page](https://app.hyperliquid.xyz/API). Never your seed. | — |
| Algorand payment wallet | Pays the activation and exit-service fees (Algorand mainnet USDC, ASA 31566704) | 2 USDC, wallet already activated |

Keys are stored in your operating system's protected credential store (Windows Credential Manager, macOS Keychain, Linux Secret Service) — never in `config.txt` or `log.txt`.

## Fees

Both QTS fees are charged **only when a position closes**. Opening a trade never requires payment, and a protective stop or exit is never held up waiting on one.

| Fee | Rate | Goes to |
|---|---|---|
| QTS developer commission | 0.10% of the closing fill (Hyperliquid Builder code) | QTS developer |
| Hyperliquid exchange fee | ~0.045% taker, per fill | Hyperliquid |
| QTS service payment (x402) | 0.01 USDC per one-time activation and per closed-position receipt | QTS service, via Algorand |
| Algorand network fee | Sponsored — no extra cost to you | — |

Default spending caps are **0.50 USDC/day** and **10 USDC total**. You can lower these during setup.

## How it works

```
Your computer                          Publisher's server
─────────────                          ──────────────────
agente.py                              agente.py --servir
  ├─ fetches BTC candles                 ├─ computes the entry signal
  ├─ asks the service: enter? ─────────► └─ answers (activation required)
  ├─ places and manages the order
  ├─ runs the stop locally, always
  └─ pays on close ────────────────────► x402 settlement on Algorand
```

The entry-signal engine is not part of this repository: it runs server-side, which is what the x402 payment buys.

## While it's running

- The console and `log.txt` show scans, entries, stop changes, payments and exits. Open the log anytime with **`VER_LOG.bat`**.
- **Ctrl+C** stops the agent. Any live exchange stop-loss stays in place — it just stops adjusting.
- Restarting preserves your trading and payment state. Don't delete `config.txt` or the `estado/` folder.

## Good to know

- Historical simulation (BTC/USDC, 5-minute candles, 12 months): **+238.57%** return, 35.04% max drawdown, 65% win rate. **This predates the current fees and live checks — it's a simulation, not a forecast, and it does not establish the performance of this fee-bearing version.** Full numbers: `docs/BACKTEST_REFERENCE.json`.
- Trading perpetual futures with leverage can lose money quickly, including more than you intend. Start in paper mode.
- Verification status and known open items: `docs/VERIFICACION.json`.
- Third-party licenses: `docs/THIRD_PARTY.md`.

## Repository layout

| Path | What it is |
|---|---|
| `agente.py` | The agent. Client mode for customers; `--servir` runs the publisher's API. |
| `config.txt` | Customer settings. Shipped blank, in paper mode. |
| `instalar.*` / `*.bat` | Guided installer and launchers. |
| `tests/test_qts.py` | 46 offline tests — no network, no funded keys. |
| `docs/` | Verification records, backtest reference, publisher guide, licenses. |
| `media/` | Demo video and captions. |

Run the tests with `python -m unittest tests.test_qts`.

