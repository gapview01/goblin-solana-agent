import express from "express";
import axios from "axios";
import { randomUUID } from "crypto";
import bs58 from "bs58";
import {
  Connection,
  Keypair,
  PublicKey,
  VersionedTransaction,
  LAMPORTS_PER_SOL,
} from "@solana/web3.js";

type JsonMap = Record<string, unknown>;

const RPC_URL = process.env.RPC_URL ?? "";
const AGENT_SECRET_B58 = process.env.AGENT_SECRET_B58 ?? "";
const PORT = Number(process.env.PORT ?? "8080");
const HARD_CAP_SOL = Number(process.env.HARD_CAP_SOL ?? "0.25");
const QUOTE_TTL_MS = 60_000;
const TOKEN_ACCOUNT_SIZE = 165;

const app = express();
app.use(express.json({ limit: "1mb" }));
app.use(express.urlencoded({ extended: true }));

let connection: Connection | null = null;
let wallet: Keypair | null = null;

async function getConnection(): Promise<Connection> {
  if (!RPC_URL) {
    throw new Error("RPC_URL env is required");
  }
  if (!connection) {
    connection = new Connection(RPC_URL, "confirmed");
  }
  return connection;
}

function getWallet(): Keypair {
  if (!AGENT_SECRET_B58) {
    throw new Error("AGENT_SECRET_B58 env is required");
  }
  if (!wallet) {
    wallet = Keypair.fromSecretKey(bs58.decode(AGENT_SECRET_B58));
  }
  return wallet;
}

export async function computeBuffers(conn: Connection) {
  const rentAta = await conn.getMinimumBalanceForRentExemption(TOKEN_ACCOUNT_SIZE);
  const txFee = 5_000;
  const tip = 100_000;
  return { rentAta, txFee, tip, total: rentAta + txFee + tip };
}

function clampLamports(balanceLamports: number, requestedLamports: number): number {
  const buffers = 500_000 + 100_000 + 2_100_000; // mirror planner defaults for safety
  const affordable = Math.max(0, balanceLamports - buffers);
  const hardCap = Math.floor(Math.max(0, HARD_CAP_SOL) * LAMPORTS_PER_SOL);
  return Math.max(0, Math.min(requestedLamports, affordable, hardCap));
}

interface RouteHint {
  inputMint: string;
  outputMint: string;
  slippageBps?: number;
  inputDecimals?: number;
  computeUnitPriceMicroLamports?: number;
  swapMode?: string;
}

interface QuoteRecord {
  routeId: string;
  inAmountLamports: number;
  ts: number;
  quoteResponse: JsonMap;
  routeHint: RouteHint;
  buffersLamports: number;
}

const quotesByPayer = new Map<string, QuoteRecord>();

function normalizeRouteHint(value: unknown): RouteHint {
  if (!value || typeof value !== "object") {
    return { inputMint: "", outputMint: "" };
  }
  const obj = value as Record<string, unknown>;
  const inputMint = typeof obj.inputMint === "string" ? obj.inputMint : typeof obj.input === "string" ? obj.input : "";
  const outputMint = typeof obj.outputMint === "string" ? obj.outputMint : typeof obj.output === "string" ? obj.output : "";
  const slippageBps = obj.slippageBps != null ? Number(obj.slippageBps) : undefined;
  const inputDecimals = obj.inputDecimals != null ? Number(obj.inputDecimals) : undefined;
  const computeUnitPriceMicroLamports =
    obj.computeUnitPriceMicroLamports != null ? Number(obj.computeUnitPriceMicroLamports) : undefined;
  const swapMode = typeof obj.swapMode === "string" ? obj.swapMode : undefined;
  return {
    inputMint,
    outputMint,
    slippageBps: Number.isFinite(slippageBps ?? NaN) ? slippageBps : undefined,
    inputDecimals: Number.isFinite(inputDecimals ?? NaN) ? inputDecimals : undefined,
    computeUnitPriceMicroLamports: Number.isFinite(computeUnitPriceMicroLamports ?? NaN)
      ? computeUnitPriceMicroLamports
      : undefined,
    swapMode,
  };
}

function lamportsToInputAmount(lamports: number, decimals?: number): number {
  const inputDecimals = Number.isFinite(decimals ?? NaN) ? Number(decimals) : 9;
  if (inputDecimals === 9) {
    return Math.floor(lamports);
  }
  const sol = lamports / LAMPORTS_PER_SOL;
  return Math.floor(sol * Math.pow(10, inputDecimals));
}

function purgeExpiredQuotes(now: number) {
  for (const [key, quote] of quotesByPayer.entries()) {
    if (now - quote.ts > QUOTE_TTL_MS) {
      quotesByPayer.delete(key);
    }
  }
}

function extractErrorMessage(err: unknown): string {
  if (axios.isAxiosError(err)) {
    if (err.response?.data) {
      try {
        return JSON.stringify(err.response.data);
      } catch (jsonErr) {
        return String(err.response.data);
      }
    }
    return err.message;
  }
  if (err instanceof Error) {
    return err.message;
  }
  return String(err);
}

async function requestJupiterQuote(hint: RouteHint, inAmountLamports: number) {
  if (!hint.inputMint || !hint.outputMint) {
    throw new Error("routeHint.inputMint and routeHint.outputMint are required");
  }
  const amount = lamportsToInputAmount(inAmountLamports, hint.inputDecimals);
  const params: Record<string, string | number | boolean> = {
    inputMint: hint.inputMint,
    outputMint: hint.outputMint,
    amount,
    onlyDirectRoutes: false,
  };
  if (hint.slippageBps != null && Number.isFinite(hint.slippageBps)) {
    params.slippageBps = hint.slippageBps;
  }
  if (hint.swapMode) {
    params.swapMode = hint.swapMode;
  }
  const { data } = await axios.get("https://quote-api.jup.ag/v6/quote", { params, timeout: 15_000 });
  return data as JsonMap;
}

async function requestSwapTransaction(quoteResponse: JsonMap, payer: PublicKey, hint: RouteHint) {
  const body: JsonMap = {
    quoteResponse,
    userPublicKey: payer.toBase58(),
    wrapAndUnwrapSol: true,
    asLegacyTransaction: false,
  };
  if (hint.computeUnitPriceMicroLamports != null && Number.isFinite(hint.computeUnitPriceMicroLamports)) {
    body.computeUnitPriceMicroLamports = hint.computeUnitPriceMicroLamports;
  }
  const { data } = await axios.post("https://quote-api.jup.ag/v6/swap", body, { timeout: 20_000 });
  return data as JsonMap;
}

app.get("/health", async (_req, res) => {
  try {
    const conn = await getConnection();
    const signer = getWallet();
    const version = await conn.getVersion();
    res.json({ ok: true, rpc: RPC_URL, pubkey: signer.publicKey.toBase58(), version });
  } catch (err) {
    res.status(503).json({ ok: false, error: extractErrorMessage(err) });
  }
});

app.post("/quote", async (req, res) => {
  try {
    const { payer, inAmountLamports, routeHint } = req.body ?? {};
    if (!payer || inAmountLamports == null) {
      return res.status(400).json({ error: "MISSING_PARAMS", required: ["payer", "inAmountLamports"] });
    }
    let payerKey: PublicKey;
    try {
      payerKey = new PublicKey(String(payer));
    } catch (err) {
      return res.status(400).json({ error: "INVALID_PAYER", detail: extractErrorMessage(err) });
    }
    const requestedLamports = Number(inAmountLamports);
    if (!Number.isFinite(requestedLamports) || requestedLamports <= 0) {
      return res.status(400).json({ error: "INVALID_AMOUNT" });
    }

    const conn = await getConnection();
    const buffers = await computeBuffers(conn);
    const balanceLamports = await conn.getBalance(payerKey, "confirmed");
    const finalLamports = clampLamports(balanceLamports, Math.floor(requestedLamports));
    if (finalLamports <= 0) {
      return res.status(400).json({
        error: "INSUFFICIENT_SOL",
        detail: {
          requestedLamports,
          balanceLamports,
          buffersLamports: buffers.total,
        },
      });
    }

    const hint = normalizeRouteHint(routeHint);
    if (!hint.inputMint || !hint.outputMint) {
      return res.status(400).json({ error: "ROUTE_HINT_REQUIRED" });
    }

    const quoteResponse = await requestJupiterQuote(hint, finalLamports);
    const routeId = randomUUID();
    const ts = Date.now();

    quotesByPayer.set(payerKey.toBase58(), {
      routeId,
      inAmountLamports: finalLamports,
      ts,
      quoteResponse,
      routeHint: hint,
      buffersLamports: buffers.total,
    });
    purgeExpiredQuotes(ts);

    res.json({
      inAmountLamports: finalLamports,
      routeId,
      ts,
      expiresInMs: QUOTE_TTL_MS,
      quote: quoteResponse,
    });
  } catch (err) {
    const status = axios.isAxiosError(err) && err.response?.status ? err.response.status : 502;
    res.status(status).json({ error: "QUOTE_FAILED", detail: extractErrorMessage(err) });
  }
});

app.post("/swap", async (req, res) => {
  try {
    const { payer, inAmountLamports, routeId } = req.body ?? {};
    if (!payer || inAmountLamports == null || !routeId) {
      return res.status(400).json({ error: "MISSING_PARAMS", required: ["payer", "inAmountLamports", "routeId"] });
    }
    let payerKey: PublicKey;
    try {
      payerKey = new PublicKey(String(payer));
    } catch (err) {
      return res.status(400).json({ error: "INVALID_PAYER", detail: extractErrorMessage(err) });
    }
    const requestedLamports = Number(inAmountLamports);
    if (!Number.isFinite(requestedLamports) || requestedLamports <= 0) {
      return res.status(400).json({ error: "INVALID_AMOUNT" });
    }

    const now = Date.now();
    purgeExpiredQuotes(now);
    const stored = quotesByPayer.get(payerKey.toBase58());
    if (!stored) {
      return res.status(409).json({ error: "REQUOTE_REQUIRED" });
    }
    if (stored.routeId !== routeId || stored.inAmountLamports !== Math.floor(requestedLamports)) {
      quotesByPayer.delete(payerKey.toBase58());
      return res.status(409).json({ error: "REQUOTE_REQUIRED" });
    }
    if (now - stored.ts > QUOTE_TTL_MS) {
      quotesByPayer.delete(payerKey.toBase58());
      return res.status(409).json({ error: "REQUOTE_REQUIRED" });
    }

    const conn = await getConnection();
    const buffers = await computeBuffers(conn);
    const balanceLamports = await conn.getBalance(payerKey, "confirmed");
    if (Math.floor(requestedLamports) + buffers.total > balanceLamports) {
      quotesByPayer.delete(payerKey.toBase58());
      return res.status(400).json({
        error: "INSUFFICIENT_SOL",
        detail: {
          requestedLamports,
          balanceLamports,
          buffersLamports: buffers.total,
        },
      });
    }

    const swapResponse = await requestSwapTransaction(stored.quoteResponse, payerKey, stored.routeHint);
    const swapTransaction = swapResponse.swapTransaction;
    if (typeof swapTransaction !== "string" || !swapTransaction) {
      quotesByPayer.delete(payerKey.toBase58());
      return res.status(502).json({ error: "SWAP_FAILED", detail: { message: "Missing swap transaction" } });
    }

    const tx = VersionedTransaction.deserialize(Buffer.from(swapTransaction, "base64"));
    const signer = getWallet();
    tx.sign([signer]);

    try {
      const signature = await conn.sendRawTransaction(tx.serialize(), { skipPreflight: false });
      const confirmation = await conn.confirmTransaction(signature, "confirmed");
      if (confirmation.value.err) {
        throw new Error(JSON.stringify(confirmation.value.err));
      }
      quotesByPayer.delete(payerKey.toBase58());
      return res.json({ signature, routeId });
    } catch (swapErr) {
      let logs: string[] | undefined;
      try {
        const sim = await conn.simulateTransaction(tx);
        logs = sim.value.logs ?? undefined;
      } catch (simErr) {
        logs = undefined;
        console.error("Simulation failed", extractErrorMessage(simErr));
      }
      quotesByPayer.delete(payerKey.toBase58());
      return res.status(502).json({
        error: "SWAP_FAILED",
        detail: {
          message: extractErrorMessage(swapErr),
          logs,
        },
      });
    }
  } catch (err) {
    res.status(500).json({ error: "UNEXPECTED", detail: extractErrorMessage(err) });
  }
});

app.post("/simulate", (req, res) => {
  try {
    const options = Array.isArray(req.body?.options) ? (req.body.options as JsonMap[]) : [];
    const baseline = (req.body?.baseline as JsonMap) ?? { name: "Hold SOL" };
    const horizonDays = Number(req.body?.horizon_days ?? 30);
    const horizon = Number.isFinite(horizonDays) && horizonDays > 0 ? Math.floor(horizonDays) : 30;
    const timeline = Array.from({ length: horizon + 1 }, (_v, idx) => idx);

    const baseSeries = {
      name: typeof baseline?.name === "string" ? (baseline.name as string) : "Baseline",
      t: timeline,
      v: timeline.map(() => 1.0),
    };

    const growthCurve = [0.01, 0.03, 0.06];
    const scenarioSeries = options.slice(0, 3).map((opt, idx) => {
      const name = typeof opt?.name === "string" && opt.name ? (opt.name as string) : `Scenario ${idx + 1}`;
      const growth = growthCurve[idx] ?? growthCurve[growthCurve.length - 1];
      const values = timeline.map((t) => Number((1 + growth * (t / Math.max(1, horizon))).toFixed(4)));
      return { name, t: timeline, v: values };
    });
    const scenarioLabel = scenarioSeries.length ? scenarioSeries.map((s) => s.name).join(" / ") : "no scenarios";

    res.json({
      title: `Baseline vs Scenarios (${horizon}d)`,
      caption: `Baseline (${baseSeries.name}) compared with ${scenarioLabel}`,
      series: [baseSeries, ...scenarioSeries],
    });
  } catch (err) {
    res.status(500).json({ error: "SIMULATION_FAILED", detail: extractErrorMessage(err) });
  }
});

app.listen(PORT, () => {
  console.log(`Executor listening on ${PORT}`);
});
