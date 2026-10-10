// Entry point: starts the bridge. See config.js for the environment variables.

import http from 'node:http';
import { createApp } from './app.js';
import { ApnsClient } from './apns.js';
import { loadConfig } from './config.js';
import { Store } from './store.js';

const TICK_MS = 15_000;

function log(msg) {
  console.log(`${new Date().toISOString()} ${msg}`);
}

function main() {
  const config = loadConfig();
  const store = new Store(config.dataFile);
  const apns = config.apns ? new ApnsClient(config.apns) : null;
  if (!apns) log('APNs is not configured: jobs will wait until you open the app.');
  const app = createApp({ config, store, apns, log });
  const server = http.createServer(app.handle);
  server.listen(config.port, config.host, () => log(`telegram4muse bridge listening on ${config.host}:${config.port}`));
  const timer = setInterval(() => app.tick().catch((err) => log(`tick failed: ${err.stack ?? err}`)), TICK_MS);
  const stop = () => {
    clearInterval(timer);
    apns?.close();
    server.close(() => process.exit(0));
  };
  process.on('SIGTERM', stop);
  process.on('SIGINT', stop);
}

try {
  main();
} catch (err) {
  console.error(`telegram4muse bridge: ${err.message}`);
  process.exit(1);
}
