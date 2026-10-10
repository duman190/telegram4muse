// Configuration, read from the environment.
//
// Required:
//   AGENT_TOKEN    secret Muse uses (Authorization: Bearer ...)
//   DEVICE_TOKEN   secret the phone uses; paste it into the app once
// Push (needed for the phone to be woken up automatically):
//   APNS_KEY_ID, APNS_TEAM_ID, APNS_TOPIC (the app's bundle id), and the .p8
//   key as APNS_PRIVATE_KEY (contents) or APNS_PRIVATE_KEY_FILE (path)
// Optional:
//   PORT (8787), HOST (0.0.0.0), DATA_FILE (./data/state.json),
//   ALERT_AFTER_SECONDS (600), JOB_EXPIRES_AFTER_SECONDS (21600),
//   JOB_LEASE_SECONDS (300), APNS_HOST (override, for testing)

import fs from 'node:fs';

const MIN_TOKEN_LENGTH = 24;

function secret(env, name) {
  const value = env[name]?.trim();
  if (!value) throw new Error(`${name} is not set`);
  if (value.length < MIN_TOKEN_LENGTH) {
    throw new Error(`${name} is too short; use at least ${MIN_TOKEN_LENGTH} random characters (e.g. openssl rand -hex 32)`);
  }
  return value;
}

export function loadConfig(env = process.env) {
  const seconds = (name, fallback) => {
    const v = env[name] === undefined ? fallback : Number(env[name]);
    if (!Number.isFinite(v) || v <= 0) throw new Error(`${name} must be a positive number of seconds`);
    return v * 1000;
  };
  const agentToken = secret(env, 'AGENT_TOKEN');
  const deviceToken = secret(env, 'DEVICE_TOKEN');
  if (agentToken === deviceToken) throw new Error('AGENT_TOKEN and DEVICE_TOKEN must be different');

  let apns = null;
  if (env.APNS_KEY_ID) {
    const privateKey = env.APNS_PRIVATE_KEY
      ? env.APNS_PRIVATE_KEY.replace(/\\n/g, '\n')
      : env.APNS_PRIVATE_KEY_FILE && fs.readFileSync(env.APNS_PRIVATE_KEY_FILE, 'utf8');
    if (!privateKey) throw new Error('set APNS_PRIVATE_KEY or APNS_PRIVATE_KEY_FILE');
    if (!env.APNS_TEAM_ID || !env.APNS_TOPIC) throw new Error('APNS_TEAM_ID and APNS_TOPIC are required with APNS_KEY_ID');
    apns = { keyId: env.APNS_KEY_ID, teamId: env.APNS_TEAM_ID, topic: env.APNS_TOPIC, privateKey, host: env.APNS_HOST };
  }

  return {
    port: Number(env.PORT || 8787),
    host: env.HOST || '0.0.0.0',
    dataFile: env.DATA_FILE || './data/state.json',
    agentToken,
    deviceToken,
    apns,
    alertAfterMs: seconds('ALERT_AFTER_SECONDS', 600),
    defaultExpiresAfterMs: seconds('JOB_EXPIRES_AFTER_SECONDS', 6 * 3600),
    leaseMs: seconds('JOB_LEASE_SECONDS', 300),
  };
}
