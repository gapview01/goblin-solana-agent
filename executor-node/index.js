// executor-node/index.js
import express from "express";
import { Connection, Keypair, PublicKey } from "@solana/web3.js";
import bs58 from "bs58";

const RPC_URL = process.env.RPC_URL || "";
const AGENT_SECRET_B58 = process.env.AGENT_SECRET_B58 || "";

// Initialize connection + wallet, but DO NOT crash the process on boot.
// This lets Cloud Run hit /health even if misconfigured.
let conn = null;
let wallet = null;

try {
  if (!RPC_URL) throw new Error("Missing RPC_URL");
  if (!AGENT_SECRET_B58) throw new Error("Missing AGENT_SECRET_B58");

  conn = new Connection(RPC_URL, "confirmed");
  wallet = Keypair.fromSecretKey(bs58.decode(AGENT_SECRET_B58));
} catch (e) {
  console.error("Startup warning:", e.message);
}

const app = express();
app.use(express.json());

// Always available; reports whether the executor is ready.
app.get("/health", (_req, res) => {
  const ok = Boolean(conn && wallet);
  res.status(ok ? 200 : 503).json({
    ok,
    hasRPC: Boolean(RPC_URL),
    hasKey: Boolean(AGENT_SECRET_B58),
    pubkey: wallet?.publicKey?.toBase58() || null,
  });
});

// Get balance for a given pubkey OR the agent wallet by default.
// Accepts GET /balance?pubkey=... or POST { pubkey }
app.all("/balance", async (req, res) => {
  try {
    if (!conn || !wallet) throw new Error("Executor not initialized");

    const bodyPk = req.body?.pubkey;
    const queryPk = req.query?.pubkey;
    const target = (bodyPk || queryPk || wallet.publicKey.toBase58()).toString();

    const pubkey = new PublicKey(target);
    const lamports = await conn.getBalance(pubkey, "confirmed");

    return res.json({
      pubkey: pubkey.toBase58(),
      lamports,
      sol: lamports / 1_000_000_000,
    });
  } catch (e) {
    console.error("Balance error:", e.message);
    return res.status(400).json({ error: String(e) });
  }
});

// IMPORTANT: Cloud Run expects us to bind to PORT (defaults to 8080)
const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
  console.log("Executor running on", PORT);
});