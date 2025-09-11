import express from 'express';
import { Keypair, PublicKey } from '@solana/web3.js';
import bs58 from 'bs58';
import axios from 'axios';

const { RPC_URL, AGENT_SECRET_B58 } = process.env;

if (!RPC_URL) {
  console.error('RPC_URL environment variable is required');
  process.exit(1);
}

if (!AGENT_SECRET_B58) {
  console.error('AGENT_SECRET_B58 environment variable is required');
  process.exit(1);
}

let keypair;
try {
  const secret = bs58.decode(AGENT_SECRET_B58);
  keypair = Keypair.fromSecretKey(secret);
} catch (err) {
  console.error('Failed to decode AGENT_SECRET_B58:', err.message);
  process.exit(1);
}

const app = express();
app.use(express.json());

app.get('/health', (req, res) => {
  res.json({ ok: true, pubkey: keypair.publicKey.toBase58() });
});

app.post('/balance', async (req, res) => {
  const { pubkey } = req.body || {};
  if (!pubkey) {
    return res.status(400).json({ error: 'pubkey required' });
  }

  try {
    const pk = new PublicKey(pubkey);
    const { data } = await axios.post(RPC_URL, {
      jsonrpc: '2.0',
      id: 1,
      method: 'getBalance',
      params: [pk.toBase58()]
    });

    const lamports = data?.result?.value;
    if (typeof lamports !== 'number') {
      return res.status(502).json({ error: 'invalid RPC response' });
    }

    res.json({ pubkey: pk.toBase58(), lamports });
  } catch (err) {
    console.error('Error fetching balance:', err.message);
    res.status(500).json({ error: 'failed to fetch balance' });
  }
});

const PORT = 8080;
app.listen(PORT, () => {
  console.log(`Executor listening on port ${PORT}`);
});
