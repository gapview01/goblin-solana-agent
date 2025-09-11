# Executor Node

A minimal Solana wallet/executor service.

## Environment Variables

- `RPC_URL` – URL of the Solana JSON-RPC endpoint.
- `AGENT_SECRET_B58` – Base58 encoded private key for the agent wallet.

## Usage

Install dependencies and start the server:

```bash
npm install
npm start
```

The service listens on `8080` and exposes:

- `GET /health` – returns `{ ok: true, pubkey }`.
- `POST /balance` – with JSON body `{ pubkey }`, returns `{ pubkey, lamports }`.

## Docker

Build and run with Docker:

```bash
docker build -t executor-node .
docker run -p 8080:8080 --env RPC_URL=... --env AGENT_SECRET_B58=... executor-node
```
