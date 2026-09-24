# QUANT TRADING SIGNALS

A local BTC/USDC trading agent: **Binance USD-M BTCUSDC data**, with **orders and native stops on Hyperliquid**. Private entry signals come from the QTS service; your computer manages open positions.

## Install on Windows

1. Download the ZIP from [Windows Installer](https://github.com/uni100h-ops/quant-trading-signals/releases/tag/Windows_Installer) and extract it into its own folder.
2. Open **INSTALAR.bat**. It installs Python 3.12 if needed and the pinned dependencies, then guides you through wallet setup.
3. Supply your Hyperliquid **public account address and a separate, authorized API wallet key**, plus the key for a **separate Algorand payment wallet**. Never supply your main trading wallet's recovery phrase.
4. Review the fees, approve the developer fee in your wallet, and wait for **SETUP COMPLETE**. Setup does not start trading.
5. Open **INICIAR.bat** and keep the window open and the computer awake.

You need at least **10 USDC in Hyperliquid futures equity** and **2 USDC in the Algorand payment wallet**. The Algorand wallet must be activated and opted in to USDC; its minimum ALGO reserve is separate from the 2 USDC. Payment transaction fees are sponsored.

Keys stay in **Windows Credential Manager**, never in `config.txt` or the log. Use the same Windows account on restart. `config.txt` contains only public wallet addresses, mode, position percentage and payment spending limits. The installer adds its setup verification fields automatically; do not edit those fields.

## Start, stop and restart

- **INICIAR.bat** starts the configured mode. Each completed Binance 5-minute candle is evaluated; a qualifying new crossover is required. Existing EMA alignment alone does not authorize an entry.
- **VER_LOG.bat** follows `log.txt` while the agent runs. Each line starts with date and time. Scans explain both BUY and SELL rejections; open positions show their stop status.
- To restart, press **Ctrl+C in the agent window**, wait for **STOPPED**, close that window, then open **INICIAR.bat once** from the **same folder**. Keep `config.txt`, `.venv` and `estado/`. Do not run setup again for an ordinary restart.
- Restart checks saved state against Hyperliquid before acting. It does not replay missed historical entries. A confirmed exchange stop remains while the agent is stopped, but it does not keep advancing.
- `log.txt` starts a new session on restart. Copy it first if you want to keep the previous session. Trading and payment state remain in `estado/`.


This build is **2.2.0-binance-restored.1**. It includes `binance_seed.zip`, a verified public-data cache with the original indicator seed. The cache then updates from Binance. Keep it beside `agente.py`. Historical entries are never opened on installation or restart.

For an update, extract the new ZIP separately. Stop the agent as above, then replace `agente.py`, `binance_seed.zip`, launchers and documentation in the existing installation. **Keep your existing `config.txt`, `estado/` and `.venv`**. Run `INSTALAR.bat` only if dependencies or accepted terms changed. Never create a second live installation for the same account. An existing Hyperliquid-data position keeps its old stop inputs until it closes; additions are paused during that transition. The new Binance feed starts when the account is flat.

## Position size and fees

Default entry size is **100% of account equity as notional**, with **isolated 5x margin**, and a maximum of three entries. A 100 USDC account requests approximately 100 USDC notional per entry, not 500 USDC. Additions share the position's protective stop.

| Charge | Amount |
|---|---|
| Hyperliquid exchange fee | Account-dependent; simulation assumes 0.045% per fill |
| QTS developer commission | 0.10% of closing fills through Hyperliquid Builder codes |
| QTS activation | 0.01 USDC, once for the accepted setup |
| QTS closed-position receipt | 0.01 USDC per fully closed position, via Algorand |
| Algorand payment transaction fee | Sponsored |
| Perpetual funding | Variable; may be paid or received |

Default x402 spending limits are **0.50 USDC per UTC day** and **10 USDC total**. Protective stops and exits are not blocked by payment failures. Builder collection on modified native stops depends on exchange behavior and is not guaranteed by this client.

## Historical simulation

**The original annual Binance benchmark is restored: +238.57%.**

Period: **September 21, 2025 to September 21, 2026**, both at 17:25 UTC. Starting capital: 10,000 USDC. 43 closed positions / 107 entry legs. Maximum drawdown: **35.04%**. The full agent replay also rounds to +238.57%.

| Same Binance sample | Return | Maximum drawdown |
|---|---:|---:|
| Original cost model | **+238.57%** | 35.04% |
| With current QTS developer and x402 fees | **+204.77%** | 35.46% |

The original model includes 0.045% exchange fees per fill, 2 bps slippage and historical Binance funding. Current QTS fees were added later; they are included only in the second row. Results are compounded simulations on a retrospectively optimized dataset, not independent validation or live Hyperliquid performance. The original September record includes losses as well as a profitable September 8–16 position.

Binance supplies indicators and the funding filter. Hyperliquid determines real fills, mark-trigger stops and actual funding payments. Entries are skipped if price deviates more than 0.30% from the Binance signal close. Binance outages or source-version mismatches pause new entries; confirmed Hyperliquid stops remain active. Different exchange prices, costs and availability can produce different live trades.

macOS/Linux: install Python 3.12, run `./instalar.sh`, then `./iniciar.sh`. Dependency notices are in `docs/THIRD_PARTY.md`.
