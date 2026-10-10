// Minimal Apple Push Notification service client: token-based auth (ES256
// JWT signed with your .p8 key) over HTTP/2, using only Node built-ins.

import http2 from 'node:http2';
import crypto from 'node:crypto';

const HOSTS = {
  production: 'https://api.push.apple.com',
  sandbox: 'https://api.sandbox.push.apple.com',
};

// Apple wants a fresh provider token at least every 60 minutes, and refuses
// tokens refreshed more often than every 20.
const TOKEN_TTL_MS = 50 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 10_000;

function b64url(input) {
  return Buffer.from(input).toString('base64url');
}

export class ApnsClient {
  // host: overrides the Apple host for both environments (used by tests).
  constructor({ keyId, teamId, privateKey, topic, host, now = Date.now }) {
    this.keyId = keyId;
    this.teamId = teamId;
    this.key = crypto.createPrivateKey(privateKey);
    this.topic = topic;
    this.hostOverride = host;
    this.now = now;
    this.cachedToken = null;
    this.sessions = new Map();
  }

  providerToken() {
    const now = this.now();
    if (this.cachedToken && now - this.cachedToken.issuedAt < TOKEN_TTL_MS) return this.cachedToken.value;
    const header = b64url(JSON.stringify({ alg: 'ES256', kid: this.keyId }));
    const claims = b64url(JSON.stringify({ iss: this.teamId, iat: Math.floor(now / 1000) }));
    const input = `${header}.${claims}`;
    const signature = crypto.sign('sha256', Buffer.from(input), { key: this.key, dsaEncoding: 'ieee-p1363' });
    const value = `${input}.${b64url(signature)}`;
    this.cachedToken = { value, issuedAt: now };
    return value;
  }

  session(env) {
    const origin = this.hostOverride ?? HOSTS[env] ?? HOSTS.production;
    const existing = this.sessions.get(origin);
    if (existing && !existing.closed && !existing.destroyed) return existing;
    const session = http2.connect(origin);
    const forget = () => {
      if (this.sessions.get(origin) === session) this.sessions.delete(origin);
    };
    session.on('error', forget);
    session.on('close', forget);
    session.on('goaway', forget);
    // Don't keep the process alive just for an idle push connection.
    session.unref();
    this.sessions.set(origin, session);
    return session;
  }

  // Sends one notification. Resolves to {status, reason, apnsId}; never rejects
  // for APNs-level errors (those come back as a non-200 status and reason).
  send({ deviceToken, env = 'production', pushType, priority, payload, expiration = 0, collapseId }) {
    return new Promise((resolve, reject) => {
      let session;
      try {
        session = this.session(env);
      } catch (err) {
        reject(err);
        return;
      }
      const headers = {
        ':method': 'POST',
        ':path': `/3/device/${deviceToken}`,
        authorization: `bearer ${this.providerToken()}`,
        'apns-topic': this.topic,
        'apns-push-type': pushType,
        'apns-priority': String(priority),
        'apns-expiration': String(expiration),
        'content-type': 'application/json',
      };
      if (collapseId) headers['apns-collapse-id'] = collapseId;
      const req = session.request(headers);
      req.setTimeout(REQUEST_TIMEOUT_MS, () => req.close(http2.constants.NGHTTP2_CANCEL));
      let status = 0;
      let apnsId = null;
      const chunks = [];
      req.on('response', (h) => {
        status = h[':status'];
        apnsId = h['apns-id'] ?? null;
      });
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        let reason = null;
        const text = Buffer.concat(chunks).toString('utf8');
        if (text) {
          try {
            reason = JSON.parse(text).reason ?? null;
          } catch {
            reason = text.slice(0, 200);
          }
        }
        resolve({ status, reason, apnsId });
      });
      req.on('error', reject);
      req.on('close', () => {
        if (!status) reject(new Error('APNs request closed without a response'));
      });
      req.end(JSON.stringify(payload));
    });
  }

  // Wakes the app in the background to fetch and run jobs.
  sendBackground(deviceToken, env, jobId) {
    return this.send({
      deviceToken,
      env,
      pushType: 'background',
      priority: 5,
      payload: { aps: { 'content-available': 1 }, t4m: { job: jobId } },
      expiration: Math.floor(this.now() / 1000) + 3600,
    });
  }

  // A visible notification, used when the silent push didn't get the job run.
  sendAlert(deviceToken, env, { title, body, jobId }) {
    return this.send({
      deviceToken,
      env,
      pushType: 'alert',
      priority: 10,
      payload: { aps: { alert: { title, body }, sound: 'default' }, t4m: { job: jobId } },
      collapseId: 't4m-pending',
    });
  }

  close() {
    for (const s of this.sessions.values()) s.close();
    this.sessions.clear();
  }
}
