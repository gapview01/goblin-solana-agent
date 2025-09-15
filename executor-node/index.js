// executor-node/index.js
import express from "express";
import { Connection, Keypair, PublicKey, VersionedTransaction } from "@solana/web3.js";
import bs58 from "bs58";
import axios from "axios";

const RPC_URL = process.env.RPC_URL || "";
const AGENT_SECRET_B58 = process.env.AGENT_SECRET_B58 || "";
const PRIORITY_FEE_MICRO_LAMPORTS = Number(process.env.PRIORITY_FEE_MICRO_LAMPORTS || "0");

let conn = null;
let wallet = null;

/** Initialize RPC + wallet (lazy + idempotent). */
function ensureInit() {
  try {
    if (!conn) {
      if (!RPC_URL) throw new Error("Missing RPC_URL");
      conn = new Connection(RPC_URL, "confirmed");
    }
    if (!wallet) {
      if (!AGENT_SECRET_B58) throw new Error("Missing AGENT_SECRET_B58");
      wallet = Keypair.fromSecretKey(bs58.decode(AGENT_SECRET_B58));
    }
    return true;
  } catch (e) {
    console.error("Init error:", e.message);
    return false;
  }
}

// Attempt once on boot, but don’t crash
if (!ensureInit()) {
  console.error("Startup warning: initialization incomplete; will retry on demand");
}

const app = express();

// Parse JSON and URL-encoded (form) bodies, and accept */*+json
app.use(express.json({ type: ["application/json", "application/*+json"] }));
app.use(express.urlencoded({ extended: true }));

// ---------------- Health ----------------
app.get("/health", (_req, res) => {
  const ok = ensureInit();
  res.status(ok ? 200 : 503).json({
    ok,
    hasRPC: Boolean(RPC_URL),
    hasKey: Boolean(AGENT_SECRET_B58),
    pubkey: wallet?.publicKey?.toBase58() || null,
  });
});

// ---------------- Balance ----------------
// GET /balance?pubkey=<address>  (default = agent wallet)
app.all("/balance", async (req, res) => {
  try {
    if (!ensureInit()) {
      await new Promise((r) => setTimeout(r, 150));
      if (!ensureInit()) throw new Error("Executor not initialized");
    }
    const target =
      (req.body && req.body.pubkey) ||
      (req.query && req.query.pubkey) ||
      wallet.publicKey.toBase58();

    const pubkey = new PublicKey(String(target));
    const lamports = await conn.getBalance(pubkey, "confirmed");

    res.json({
      ok: true,
      pubkey: pubkey.toBase58(),
      lamports,
      sol: lamports / 1_000_000_000,
    });
  } catch (e) {
    console.error("Balance error:", e.message);
    res.status(500).json({ ok: false, error: String(e) });
  }
});

// ---------------- Quote (Jupiter) ----------------
const MINTS = {
  SOL: "So11111111111111111111111111111111111111112",
  USDC: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
};
const DECIMALS = { SOL: 9, USDC: 6 };

let TOKENS_CACHE = null;
let TOKENS_CACHE_TS = 0;
async function resolveMintBySymbol(symbol) {
  const sym = String(symbol).toUpperCase();
  const now = Date.now();
  if (!TOKENS_CACHE || now - TOKENS_CACHE_TS > 10 * 60 * 1000) {
    const { data } = await axios.get("https://token.jup.ag/all", { timeout: 15000 });
    TOKENS_CACHE = Array.isArray(data) ? data : [];
    TOKENS_CACHE_TS = now;
  }
  let found = TOKENS_CACHE.find((t) => (t.symbol || "").toUpperCase() === sym);
  if (found) return found.address;
  const alt = TOKENS_CACHE.find((t) => (t.symbol || "").toUpperCase().includes(sym));
  return alt ? alt.address : null;
}

function getDecimals(sym) {
  return DECIMALS[String(sym).toUpperCase()] ?? 9;
}

async function jupQuote(inputMint, outputMint, amountBase, slippageBps) {
  const { data } = await axios.get("https://quote-api.jup.ag/v6/quote", {
    params: {
      inputMint,
      outputMint,
      amount: amountBase,
      slippageBps: slippageBps ?? 50,
      onlyDirectRoutes: false,
    },
    timeout: 15000,
  });
  return data;
}

async function jupSwapFromQuote(quote) {
  const { data: swapResp } = await axios.post(
    "https://quote-api.jup.ag/v6/swap",
    {
      quoteResponse: quote,
      userPublicKey: wallet.publicKey.toBase58(),
      wrapAndUnwrapSol: true,
      // computeUnitPriceMicroLamports: PRIORITY_FEE_MICRO_LAMPORTS || undefined,
      asLegacyTransaction: false,
    },
    { timeout: 20000 }
  );

  const swapTxB64 = swapResp?.swapTransaction;
  if (!swapTxB64) throw new Error("missing swapTransaction from Jupiter");

  const tx = VersionedTransaction.deserialize(Buffer.from(swapTxB64, "base64"));
  tx.sign([wallet]);
  const sig = await conn.sendRawTransaction(tx.serialize(), { skipPreflight: false });
  const conf = await conn.confirmTransaction(sig, "confirmed");
  if (conf?.value?.err) throw new Error(`transaction failed: ${JSON.stringify(conf.value.err)}`);
  return sig;
}

app.post("/quote", async (req, res) => {
  try {
    let { from, to, amount, slippageBps } = req.body || {};
    if (!from || !to || amount == null) {
      return res.status(400).json({ error: "from, to, amount required" });
    }
    from = String(from).toUpperCase();
    to = String(to).toUpperCase();

    let inputMint = MINTS[from] || req.body.from;
    let outputMint = MINTS[to] || req.body.to;
    if (!inputMint) inputMint = await resolveMintBySymbol(from);
    if (!outputMint) outputMint = await resolveMintBySymbol(to);
    if (!inputMint || !outputMint) {
      return res.status(400).json({ error: "could not resolve mint(s)", from, to });
    }

    const decimals = getDecimals(from);
    const amountBase = Math.round(Number(amount) * 10 ** decimals);
    if (!Number.isFinite(amountBase) || amountBase <= 0) {
      return res.status(400).json({ error: "invalid amount" });
    }

    const quote = await jupQuote(inputMint, outputMint, amountBase, slippageBps);
    res.json(quote);
  } catch (e) {
    console.error("Quote error:", e.message);
    res.status(502).json({ error: String(e) });
  }
});

// ---------------- Swap (Jupiter) ----------------
app.post("/swap", async (req, res) => {
  try {
    if (!ensureInit()) {
      await new Promise((r) => setTimeout(r, 150));
      if (!ensureInit()) throw new Error("Executor not initialized");
    }

    let { from, to, amount, slippageBps } = req.body || {};
    if (!from || !to || amount == null) {
      return res.status(400).json({ error: "from, to, amount required" });
    }
    from = String(from).toUpperCase();
    to = String(to).toUpperCase();

    let inputMint = MINTS[from] || from;
    let outputMint = MINTS[to] || to;
    if (!MINTS[from]) inputMint = (await resolveMintBySymbol(from)) || inputMint;
    if (!MINTS[to]) outputMint = (await resolveMintBySymbol(to)) || outputMint;

    const decimals = getDecimals(from);
    const amountBase = Math.round(Number(amount) * 10 ** decimals);
    if (!Number.isFinite(amountBase) || amountBase <= 0) {
      return res.status(400).json({ error: "invalid amount" });
    }

    const quote = await jupQuote(inputMint, outputMint, amountBase, slippageBps);
    const sig = await jupSwapFromQuote(quote);
    return res.json({ txSignature: sig });
  } catch (e) {
    console.error("Swap error:", e.message);
    return res.status(502).json({ error: String(e) });
  }
});

// ---------------- Stake / Unstake (JITO via Jupiter) ----------------
function readStakeBody(req) {
  // Accept JSON, form, or query
  const body = (req.body && Object.keys(req.body).length ? req.body : req.query) || {};
  const protocol = (body.protocol ?? "").toString().toLowerCase();
  const amtRaw = body.amountLamports ?? body.amountlamports ?? body.amount ?? null;
  const amountLamports = amtRaw == null ? null : Number(amtRaw); // accept string or number
  return { protocol, amountLamports };
}

/**
 * Stake MVP: swap SOL -> jitoSOL via Jupiter
 * Body: { "protocol": "jito", "amountLamports": <int> }
 */
app.post("/stake", async (req, res) => {
  try {
    if (!ensureInit()) {
      await new Promise((r) => setTimeout(r, 150));
      if (!ensureInit()) throw new Error("Executor not initialized");
    }

    const { protocol, amountLamports } = readStakeBody(req);
    console.log("STAKE req", req.headers["content-type"], req.body, req.query);

    if (!protocol || amountLamports == null) {
      return res.status(400).json({ error: "protocol and amountLamports required" });
    }
    if (protocol !== "jito") {
      return res.status(400).json({ error: "unsupported protocol", supported: ["jito"] });
    }

    const jitoMint = await resolveMintBySymbol("JITOSOL");
    if (!jitoMint) return res.status(400).json({ error: "could not resolve JITOSOL mint" });

    // Convert lamports -> SOL float for quote
    const amountSol = Number(amountLamports) / 1_000_000_000;
    const inputMint = MINTS.SOL;       // SOL mint
    const outputMint = jitoMint;       // jitoSOL mint
    const amountBase = Math.round(amountSol * 10 ** getDecimals("SOL"));

    const quote = await jupQuote(inputMint, outputMint, amountBase, /*slippage*/ 50);
    const sig = await jupSwapFromQuote(quote);

    return res.json({ txSignature: sig });
  } catch (e) {
    console.error("Stake error:", e.message);
    return res.status(502).json({ error: String(e) });
  }
});

/**
 * Unstake MVP: swap jitoSOL -> SOL via Jupiter
 * Body: { "protocol": "jito", "amountLamports": <int> }
 */
app.post("/unstake", async (req, res) => {
  try {
    if (!ensureInit()) {
      await new Promise((r) => setTimeout(r, 150));
      if (!ensureInit()) throw new Error("Executor not initialized");
    }

    const { protocol, amountLamports } = readStakeBody(req);
    console.log("UNSTAKE req", req.headers["content-type"], req.body, req.query);

    if (!protocol || amountLamports == null) {
      return res.status(400).json({ error: "protocol and amountLamports required" });
    }
    if (protocol !== "jito") {
      return res.status(400).json({ error: "unsupported protocol", supported: ["jito"] });
    }

    const jitoMint = await resolveMintBySymbol("JITOSOL");
    if (!jitoMint) return res.status(400).json({ error: "could not resolve JITOSOL mint" });

    // amountLamports uses 9dp for jitoSOL as well (MVP)
    const amountJito = Number(amountLamports) / 1_000_000_000;
    const inputMint = jitoMint;
    const outputMint = MINTS.SOL;
    const amountBase = Math.round(amountJito * 10 ** 9); // jitoSOL dp ~ 9

    const quote = await jupQuote(inputMint, outputMint, amountBase, /*slippage*/ 50);
    const sig = await jupSwapFromQuote(quote);

    return res.json({ txSignature: sig });
  } catch (e) {
    console.error("Unstake error:", e.message);
    return res.status(502).json({ error: String(e) });
  }
});

// --------------- Start server ---------------
const PORT = process.env.PORT || 8080;
app.listen(PORT, () => console.log("Executor running on", PORT));