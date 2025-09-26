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
  console.error("Startup error: required config missing (RPC_URL or AGENT_SECRET_B58). Exiting.");
  process.exit(1);
}

const app = express();

// Parse JSON and URL-encoded (form) bodies, and accept */*+json
app.use(express.json({ type: ["application/json", "application/*+json"] }));
app.use(express.urlencoded({ extended: true }));

// ---------------- Error helpers ----------------
function fail(res, http, code, userMessage, details = {}, suggestion, retriable = false) {
  return res.status(http).json({
    ok: false,
    code,
    http,
    user_message: userMessage,
    details,
    suggestion,
    retriable,
  });
}

function firstLines(text, n = 2) {
  try {
    return String(text || "").split(/\n+/).slice(0, n).join(" · ");
  } catch {
    return String(text || "");
  }
}

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
    return fail(res, 500, "UNEXPECTED", "Balance lookup failed.", { short_reason: String(e?.message || e) }, "Try again soon.");
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

// Helper: only treat strings that look like base58 mints as mints
const looksLikeMint = (s) =>
  typeof s === "string" && /^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(s);

// Quote resolves symbols to mints unless a real mint was provided
app.post("/quote", async (req, res) => {
  try {
    let { from, to, amount, slippageBps } = req.body || {};
    if (!from || !to || amount == null) {
      return fail(res, 400, "INVALID_AMOUNT", "That amount is cursed.", {}, "Use a positive number like 0.05");
    }
    from = String(from).toUpperCase();
    to   = String(to).toUpperCase();

    // Use executor MINTS first; otherwise only accept req.body.from/to as mints if they look like mints
    let inputMint  = MINTS[from] || (looksLikeMint(req.body.from) ? req.body.from : null);
    let outputMint = MINTS[to]   || (looksLikeMint(req.body.to)   ? req.body.to   : null);

    // Resolve symbols that weren't mapped in MINTS (e.g., JITOSOL) via Jupiter token list
    if (!inputMint)  inputMint  = await resolveMintBySymbol(from);
    if (!outputMint) outputMint = await resolveMintBySymbol(to);

    if (!inputMint || !outputMint) {
      return fail(res, 400, "ROUTE_NOT_FOUND", "No viable route found.", { from, to }, "Try smaller size or a more liquid token.");
    }

    const decimals = getDecimals(from);
    const amountBase = Math.round(Number(amount) * 10 ** decimals);
    if (!Number.isFinite(amountBase) || amountBase <= 0) {
      return fail(res, 400, "INVALID_AMOUNT", "That amount is cursed.", {}, "Use a positive number like 0.05");
    }

    const quote = await jupQuote(inputMint, outputMint, amountBase, slippageBps);
    res.json(quote);
  } catch (e) {
    console.error("Quote error:", e.message);
    const reason = firstLines(e?.message || e);
    return fail(res, 502, "ROUTE_NOT_FOUND", "No viable route found.", { short_reason: reason }, "Try smaller size or a more liquid token.");
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
      return fail(res, 400, "INVALID_AMOUNT", "That amount is cursed.", {}, "Use a positive number like 0.05");
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
      return fail(res, 400, "INVALID_AMOUNT", "That amount is cursed.", {}, "Use a positive number like 0.05");
    }

    // Pre-check SOL balance to avoid wasting time if underfunded
    if (from === "SOL") {
      const lamports = Math.round(Number(amount) * 1_000_000_000);
      const bal = await conn.getBalance(wallet.publicKey, "confirmed");
      const buffersLamports = 2_700_000; // rent + fees + tip
      if (lamports + buffersLamports > bal) {
        const tryLamports = Math.max(0, bal - buffersLamports);
        return fail(
          res,
          400,
          "INSUFFICIENT_SOL",
          `Not enough SOL, goblin. Need ${(lamports + buffersLamports) / 1e9} incl. fees; you’ve got ${bal / 1e9}.`,
          {
            need_sol: (lamports + buffersLamports) / 1e9,
            have_sol: bal / 1e9,
            try_sol: Math.max(0, tryLamports) / 1e9,
          },
          `Try ${(Math.max(0, tryLamports) / 1e9).toFixed(3)} SOL or top up.`,
          false
        );
      }
    }

    const quote = await jupQuote(inputMint, outputMint, amountBase, slippageBps);
    const sig = await jupSwapFromQuote(quote);
    return res.json({ ok: true, signature: sig });
  } catch (e) {
    console.error("Swap error:", e.message);
    const reason = firstLines(e?.message || e);
    return fail(res, 502, "SWAP_FAILED", "Sim says no.", { short_reason: reason }, "Try smaller size or re‑quote.");
  }
});

// ---------------- Simulate Scenarios ----------------
app.post("/simulate", (req, res) => {
  try {
    const options = Array.isArray(req.body?.options) ? req.body.options : [];
    const baseline = req.body?.baseline ?? { name: "Hold SOL" };
    const horizonDays = Number(req.body?.horizon_days ?? 30);
    const horizon = Number.isFinite(horizonDays) && horizonDays > 0 ? Math.floor(horizonDays) : 30;
    const timeline = Array.from({ length: horizon + 1 }, (_v, idx) => idx);

    const baseSeries = {
      name: typeof baseline?.name === "string" ? baseline.name : "Hold SOL",
      t: timeline,
      v: timeline.map(() => 1.0),
    };

    // Assumptions for quick, sensible curves (no external data):
    // - LSD staking (JitoSOL/mSOL/bSOL/scnSOL): ~7% APR → daily factor
    // - Split stake & hold: 50% LSD, 50% baseline
    // - Stable (USDC) or unknown: flat baseline
    const LSD_SYMBOLS = new Set(["JITOSOL", "MSOL", "BSOL", "SCNSOL"]);

    function dailySeriesFromApr(apr, days) {
      const daily = Math.pow(1 + apr, 1 / 365) - 1;
      const out = [];
      let value = 1.0;
      for (let t = 0; t <= days; t++) {
        if (t === 0) out.push(1.0);
        else {
          value = Number((value * (1 + daily)).toFixed(6));
          out.push(value);
        }
      }
      return out;
    }

    function classifyOption(opt) {
      const name = (opt?.name || "").toString().toUpperCase();
      const plan = Array.isArray(opt?.plan) ? opt.plan : [];
      let touchesLsd = false;
      let touchesStable = false;
      let mentionsSplit = name.includes("SPLIT");
      for (const a of plan) {
        const verb = (a?.verb || "").toString().toLowerCase();
        const p = a?.params || {};
        const token = (p?.protocol || p?.to || p?.out || "").toString().toUpperCase();
        if (verb === "stake") touchesLsd = true;
        if (verb === "swap" && LSD_SYMBOLS.has(token)) touchesLsd = true;
        if (verb === "swap" && token === "USDC") touchesStable = true;
      }
      if (mentionsSplit) return "split";
      if (touchesLsd) return "lsd";
      if (touchesStable) return "stable";
      return "flat";
    }

    const lsdCurve = dailySeriesFromApr(0.07, horizon);

    const scenarioSeries = options.slice(0, 3).map((opt, idx) => {
      const name = typeof opt?.name === "string" && opt.name ? opt.name : `Scenario ${idx + 1}`;
      const kind = classifyOption(opt);
      let values;
      if (kind === "lsd") {
        values = lsdCurve;
      } else if (kind === "split") {
        values = timeline.map((t) => Number((0.5 * 1.0 + 0.5 * lsdCurve[t]).toFixed(6)));
      } else if (kind === "stable") {
        values = timeline.map(() => 1.0);
      } else {
        values = timeline.map(() => 1.0);
      }
      return { name, t: timeline, v: values };
    });

    const scenarioLabel = scenarioSeries.length ? scenarioSeries.map((s) => s.name).join(" / ") : "no scenarios";

    res.json({
      title: `Baseline vs Scenarios (${horizon}d)`,
      caption: `Baseline (${baseSeries.name}) compared with ${scenarioLabel}`,
      series: [baseSeries, ...scenarioSeries],
    });
  } catch (e) {
    res.status(500).json({ error: "SIMULATION_FAILED", detail: String(e?.message || e) });
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
      return fail(res, 400, "INVALID_AMOUNT", "That amount is cursed.");
    }
    if (protocol !== "jito") {
      return fail(res, 400, "STAKE_PROTOCOL_UNSUPPORTED", "Only Jito staking for now.", { supported: ["jito"] }, "Use JITOSOL.");
    }

    const jitoMint = await resolveMintBySymbol("JITOSOL");
    if (!jitoMint) return fail(res, 400, "ROUTE_NOT_FOUND", "No viable route found.", { token: "JITOSOL" });

    // Convert lamports -> SOL float for quote
    const amountSol = Number(amountLamports) / 1_000_000_000;

    // Pre-check SOL balance & buffers
    const bal = await conn.getBalance(wallet.publicKey, "confirmed");
    const buffersLamports = 2_700_000;
    const needLamports = Math.round(Number(amountLamports));
    if (needLamports + buffersLamports > bal) {
      const tryLamports = Math.max(0, bal - buffersLamports);
      return fail(
        res,
        400,
        "INSUFFICIENT_SOL",
        `Not enough SOL, goblin. Need ${(needLamports + buffersLamports) / 1e9} incl. fees; you’ve got ${bal / 1e9}.`,
        {
          need_sol: (needLamports + buffersLamports) / 1e9,
          have_sol: bal / 1e9,
          try_sol: Math.max(0, tryLamports) / 1e9,
        },
        `Try ${(Math.max(0, tryLamports) / 1e9).toFixed(3)} SOL or top up.`,
        false
      );
    }

    const inputMint = MINTS.SOL;       // SOL mint
    const outputMint = jitoMint;       // jitoSOL mint
    const amountBase = Math.round(amountSol * 10 ** getDecimals("SOL"));

    const quote = await jupQuote(inputMint, outputMint, amountBase, /*slippage*/ 50);
    const sig = await jupSwapFromQuote(quote);

    return res.json({ ok: true, signature: sig });
  } catch (e) {
    console.error("Stake error:", e.message);
    const reason = firstLines(e?.message || e);
    return fail(res, 502, "SWAP_FAILED", "Sim says no.", { short_reason: reason }, "Try smaller size or re‑quote.");
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
      return fail(res, 400, "INVALID_AMOUNT", "That amount is cursed.");
    }
    if (protocol !== "jito") {
      return fail(res, 400, "STAKE_PROTOCOL_UNSUPPORTED", "Only Jito staking for now.", { supported: ["jito"] }, "Use JITOSOL.");
    }

    const jitoMint = await resolveMintBySymbol("JITOSOL");
    if (!jitoMint) return fail(res, 400, "ROUTE_NOT_FOUND", "No viable route found.", { token: "JITOSOL" });

    // amountLamports uses 9dp for jitoSOL as well (MVP)
    const amountJito = Number(amountLamports) / 1_000_000_000;
    const inputMint = jitoMint;
    const outputMint = MINTS.SOL;
    const amountBase = Math.round(amountJito * 10 ** 9); // jitoSOL dp ~ 9

    const quote = await jupQuote(inputMint, outputMint, amountBase, /*slippage*/ 50);
    const sig = await jupSwapFromQuote(quote);

    return res.json({ ok: true, signature: sig });
  } catch (e) {
    console.error("Unstake error:", e.message);
    const reason = firstLines(e?.message || e);
    return fail(res, 502, "SWAP_FAILED", "Sim says no.", { short_reason: reason }, "Try smaller size or re‑quote.");
  }
});

// --------------- Start server ---------------
const PORT = process.env.PORT || 8080;
app.listen(PORT, () => console.log("Executor running on", PORT));