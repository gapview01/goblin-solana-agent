import express from 'express';
import dotenv from 'dotenv';
import { Connection, Keypair, TransactionInstruction, TransactionMessage, VersionedTransaction, PublicKey, LAMPORTS_PER_SOL } from '@solana/web3.js';

dotenv.config();

const RPC_ENDPOINT = process.env.RPC_ENDPOINT || 'https://api.mainnet-beta.solana.com';
const SOLANA_KEYPAIR = JSON.parse(process.env.SOLANA_KEYPAIR || '[]');
const allowedMints = new Set(JSON.parse(process.env.ALLOWLIST_MINTS || '[]'));
const allowedPrograms = new Set(JSON.parse(process.env.ALLOWLIST_PROGRAMS || '[]'));

const connection = new Connection(RPC_ENDPOINT, 'confirmed');
const keypair = SOLANA_KEYPAIR.length
  ? Keypair.fromSecretKey(Uint8Array.from(SOLANA_KEYPAIR))
  : Keypair.generate();

const app = express();
app.use(express.json());

const NATIVE_SOL = 'So11111111111111111111111111111111111111112';

function redact(str) {
  return str ? `${str.slice(0,4)}...${str.slice(-4)}` : str;
}

function checkMints(inputMint, outputMint) {
  if (allowedMints.size === 0) return true;
  return allowedMints.has(inputMint) && allowedMints.has(outputMint);
}

function checkPrograms(instructions) {
  if (allowedPrograms.size === 0) return true;
  for (const ix of instructions) {
    const pid = ix.programId.toBase58();
    if (!allowedPrograms.has(pid)) return false;
  }
  return true;
}

function logAttempt(type, data) {
  console.log(`${type} attempt`, {
    inputMint: redact(data.inputMint),
    outputMint: redact(data.outputMint),
    amount: data.amount
  });
}

app.post('/quote', async (req, res) => {
  const { inputMint, outputMint, amount, slippageBps } = req.body;
  logAttempt('quote', req.body);

  if (!checkMints(inputMint, outputMint)) {
    return res.status(400).json({ error: 'mint not allowed' });
  }

  const params = new URLSearchParams({
    inputMint,
    outputMint,
    amount: String(amount),
  });
  if (slippageBps !== undefined) params.append('slippageBps', String(slippageBps));

  try {
    const resp = await fetch(`https://quote-api.jup.ag/v6/quote?${params.toString()}`);
    const data = await resp.json();
    res.json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/swap', async (req, res) => {
  const { inputMint, outputMint, amount, slippageBps } = req.body;
  logAttempt('swap', req.body);

  if (!checkMints(inputMint, outputMint)) {
    return res.status(400).json({ error: 'mint not allowed' });
  }

  const params = new URLSearchParams({
    inputMint,
    outputMint,
    amount: String(amount),
  });
  if (slippageBps !== undefined) params.append('slippageBps', String(slippageBps));

  try {
    const quoteResp = await (await fetch(`https://quote-api.jup.ag/v6/quote?${params.toString()}`)).json();

    let solAmount = 0;
    if (inputMint === NATIVE_SOL) {
      solAmount = Number(amount) / LAMPORTS_PER_SOL;
    } else if (outputMint === NATIVE_SOL && quoteResp.outAmount) {
      solAmount = Number(quoteResp.outAmount) / LAMPORTS_PER_SOL;
    }
    if (solAmount > 5) {
      return res.json({ requiresApproval: true });
    }

    const swapResp = await fetch('https://quote-api.jup.ag/v6/swap-instructions', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        quoteResponse: quoteResp,
        userPublicKey: keypair.publicKey.toBase58(),
        wrapAndUnwrapSol: true
      })
    });
    const swapData = await swapResp.json();

    function toIx(ix) {
      return new TransactionInstruction({
        programId: new PublicKey(ix.programId),
        keys: ix.accounts.map(a => ({
          pubkey: new PublicKey(a.pubkey),
          isSigner: a.isSigner,
          isWritable: a.isWritable
        })),
        data: Buffer.from(ix.data, 'base64')
      });
    }

    const ixs = [];
    if (swapData.setupInstructions) {
      for (const ix of swapData.setupInstructions) ixs.push(toIx(ix));
    }
    ixs.push(toIx(swapData.swapInstruction));
    if (swapData.cleanupInstructions) {
      for (const ix of swapData.cleanupInstructions) ixs.push(toIx(ix));
    }

    if (!checkPrograms(ixs)) {
      return res.status(400).json({ error: 'program not allowed' });
    }

    const lookupTables = [];
    if (swapData.addressLookupTableAddresses) {
      for (const addr of swapData.addressLookupTableAddresses) {
        const lut = await connection.getAddressLookupTable(new PublicKey(addr));
        if (lut.value) lookupTables.push(lut.value);
      }
    }

    const latest = await connection.getLatestBlockhash();
    const messageV0 = new TransactionMessage({
      payerKey: keypair.publicKey,
      recentBlockhash: latest.blockhash,
      instructions: ixs
    }).compileToV0Message(lookupTables);

    const tx = new VersionedTransaction(messageV0);
    tx.sign([keypair]);

    const sim = await connection.simulateTransaction(tx);
    if (sim.value.err) {
      return res.status(400).json({ error: JSON.stringify(sim.value.err) });
    }

    const sig = await connection.sendRawTransaction(tx.serialize());
    await connection.confirmTransaction({ signature: sig, blockhash: latest.blockhash, lastValidBlockHeight: latest.lastValidBlockHeight });

    res.json({ txSignature: sig });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

const PORT = process.env.PORT || 8080;
app.listen(PORT, () => {
  console.log(`executor listening on ${PORT}`);
});

