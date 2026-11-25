# QHOS

My personal trading system, written in Python. It started as a research platform (v1) and I'm now rebuilding it smaller (v2), with the focus on capturing live futures data properly before training anything on it.

It's a personal project. Nothing here trades real money and none of it is financial advice.

## v1 (`v1/`)

The first version, a research setup:

- asyncio orchestrator and a FastAPI + WebSocket dashboard
- DuckDB + Parquet storage
- market regime model in PyTorch (GPU)
- genetic strategy search, with Claude API agents in the loop
- walk-forward and Monte Carlo validation
- risk engine with a kill switch, and a paper broker

## v2 (`v2/qhos2/`)

The rebuild. It runs on Linux (WSL on my machine):

- connects to Rithmic's R|Protocol API (WebSocket + protobuf) and receives live CME futures data
- saves one dataset per trading day, with checks for gaps, restarts and broken files
- local web terminal and a separate "Brain" service (FastAPI)
- around 135 test files (pytest)

Next step is training models on the captured data.

## Not in this repo

- credentials of any kind (see `v1/.env.example`)
- captured market data, models and local databases
- Rithmic's `.proto` files: you need your own Rithmic account and their protocol files to run the capture
- my machine-specific audit logs

Most docs and comments are in Portuguese.
