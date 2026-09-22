# QUANT TRADING SIGNALS (QTS)

**Automated BTC/USDC trading on Hyperliquid — runs on your own computer.**

QTS watches the market, opens qualifying trades and manages a dynamic stop as conditions change. You keep custody of your wallets and can stop the agent at any time.

## Before you start — checklist

- Windows 10/11 (or macOS/Linux — see below). Python 3.12 is installed for you on Windows.
- A Hyperliquid account with **at least 100 USDC** in futures equity, standard account mode.
- A secondary Algorand wallet, already activated (0.2 ALGO reserve + USDC opt-in), funded with **at least 2 USDC**. This pays the one-time activation and the fee charged when a position closes.
- An internet connection while the agent runs.

## Install & run (Windows)

1. **Extract the ZIP** into its own folder — don't run it from inside the ZIP.
2. Double-click **`INSTALAR.bat`**. It installs Python 3.12 if needed, sets up a local environment, and walks you through entering your Hyperliquid public address, a **separate API wallet key**, and your Algorand payment wallet key. **Never enter your main wallet's seed phrase — anywhere.**
3. Review the commission and service-fee terms it shows you, and approve them in your wallet extension when asked.
4. When you see **SETUP COMPLETE**, double-click **`INICIAR.bat`** and keep that window open — that's the agent running.

**New to QTS? Run `PROBAR_PAPER.bat` first.** It simulates trading with no keys and no real payments, so you can watch it work before risking anything. Live trading only activates once the provider has published a release with live recipients configured.

**macOS / Linux:** install Python 3.12, then run `./instalar.sh` followed by `./iniciar.sh`.

**Video walkthrough:** `media/QUANT_TRADING_SIGNALS_Demo.mp4` (or `media/WATCH_VIDEO.html`) in your extracted folder.

## Your wallets

| Wallet | What it's for | Minimum |
|---|---|---|
| Hyperliquid trading wallet | Holds your trading capital (USDC) | 100 USDC in futures equity |
| Hyperliquid API wallet | Separate key QTS uses to sign orders — create it at the [Hyperliquid API page](https://app.hyperliquid.xyz/API). Never your seed. | — |
| Algorand payment wallet | Pays the exit-service fee in USDC (Algorand mainnet, ASA 31566704) | 2 USDC, wallet already activated |

Keys are saved in your operating system's protected credential store (Windows Credential Manager, macOS Keychain, or Linux Secret Service) — never in `config.txt` or `log.txt`. QTS reuses them automatically on restart, so keep your computer and login secure.

## Fees you'll pay

Both fees below are charged only when a position **closes** — opening a trade never requires payment, and a protective stop or exit is never held up waiting on payment.

| Fee | Rate | Goes to |
|---|---|---|
| QTS developer commission | 0.10% of the closing fill only (Hyperliquid Builder code) | QTS developer — nothing on entries |
| Hyperliquid exchange fee | ~0.045% taker, per fill | Hyperliquid |
| QTS service payment (x402) | 0.01 USDC per one-time activation and per closed-position receipt | QTS service, via Algorand |
| Algorand network fee | Sponsored — no extra cost to you | — |

Before opening a trade, QTS checks the signal with the publisher's own server at no charge — that check is what decides entries; you're only billed once a position you opened actually closes. Default spending caps are **0.50 USDC/day** and **10 USDC total**. You can lower these in setup, not raise the price.

## While it's running

- The console and `log.txt` show scans, entries, stop changes, payments and exits live. Watch them anytime with **`VER_LOG.bat`**.
- **Ctrl+C** stops the agent. Any live exchange stop-loss stays in place — it just stops adjusting.
- Restarting preserves your trading and payment state. Don't delete `config.txt` or the `estado/` folder.

## Good to know

- Historical simulation (BTC/USDC, 5-minute candles, 12 months): **+238.57%** return, 35.04% max drawdown, 65% win rate. This predates the current fees and live checks — it's a simulation, not a forecast. Full numbers: `docs/BACKTEST_REFERENCE.json`.
- Third-party licenses and full technical details live in `docs/`.
