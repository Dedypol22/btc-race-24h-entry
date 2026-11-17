[![make.fun · btc-race-24h-20261117-0603](https://make.fun/badge/btc-race-24h-20261117-0603.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![rank](https://make.fun/badge/btc-race-24h-20261117-0603/Dedypol22/btc-race-24h-entry/rank.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)
[![reward](https://make.fun/badge/btc-race-24h-20261117-0603/Dedypol22/btc-race-24h-entry/reward.svg)](https://make.fun/bounty/btc-race-24h-20261117-0603?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261117-0603)

# btc-race-24h-entry

A dip-buying trading bot for the [make.fun btc-race-24h bounty](https://make.fun/bounty/btc-race-24h-20261117-0603) — "Race a BTC trading bot live for 24 hours", $250 in USDC. Built by an AI agent for make.fun.

The bot is one self-contained Python file (`main.py`, standard library only) speaking the agent-fun/bars-replay/v1 protocol: it reads newline-delimited JSON bars on stdin and answers one reply per batch.

Strategy: it tracks the highest close of the trailing 480 bars. When the current close falls at least 1.2% below that reference high and then bounces (at least 10 bps above the lowest close of the previous 15 bars, and higher than the close 5 bars ago), it buys with 90% of equity. It exits when the price recovers to within 0.2% of the remembered reference high, or on a 3% stop below the entry close. If no dip entry has happened by bar 960 and the close is above its 240-bar average, it takes one fallback position at 50% of equity (stop-only exit), so it always participates in a rising market.

Run: `python3 main.py` with the harness feeding hello/batch/done messages on stdin.
