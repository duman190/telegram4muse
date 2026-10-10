// HTTP API and job dispatcher.
//
// Two callers, two tokens:
//   - the agent (Muse) uses AGENT_TOKEN for /v1/status, /v1/chats, /v1/jobs...
//   - the phone uses DEVICE_TOKEN for /v1/device/...
// The bridge never sees Telegram credentials; it only queues jobs, wakes the
// phone with a push, and stores what the phone reports back.

import crypto from 'node:crypto';
import { normalizeJob, normalizeChats, normalizeResult, chatIdsOf, ValidationError } from './validate.js';

const MAX_BODY = 64 * 1024;
const MAX_CLAIM = 5;
const FINISHED = new Set(['succeeded', 'failed', 'canceled', 'expired']);

class HttpError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function tokenMatches(header, expected) {
  if (!header || !header.startsWith('Bearer ')) return false;
  const given = Buffer.from(header.slice(7).trim());
  const want = Buffer.from(expected);
  return given.length === want.length && crypto.timingSafeEqual(given, want);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    req.on('data', (c) => {
      size += c.length;
      if (size > MAX_BODY) {
        reject(new HttpError(413, 'body_too_large', `body must be under ${MAX_BODY} bytes`));
        req.destroy();
        return;
      }
      chunks.push(c);
    });
    req.on('end', () => {
      const text = Buffer.concat(chunks).toString('utf8');
      if (!text) return resolve({});
      try {
        resolve(JSON.parse(text));
      } catch {
        reject(new HttpError(400, 'invalid_json', 'body is not valid JSON'));
      }
    });
    req.on('error', reject);
  });
}

function iso(ms) {
  return ms === undefined || ms === null ? null : new Date(ms).toISOString();
}

// Public view of a job.
function present(job) {
  return {
    id: job.id,
    status: job.status,
    note: job.note ?? null,
    created_at: iso(job.created_at),
    run_at: iso(job.run_at),
    expires_at: iso(job.expires_at),
    pushed_at: iso(job.pushed_at),
    alerted_at: iso(job.alerted_at),
    claimed_at: iso(job.claimed_at),
    finished_at: iso(job.finished_at),
    steps: job.steps,
    result: job.result ?? null,
    error: job.error ?? null,
  };
}

export function createApp({ config, store, apns = null, now = Date.now, log = () => {} }) {
  // Sends a push to the registered phone. Builds installed from Xcode use
  // Apple's sandbox, TestFlight/App Store builds production; if Apple says the
  // token belongs to the other environment, switch and retry once.
  async function pushToDevice(kind, send) {
    const device = store.device;
    if (!apns || !device?.apns_token || device.apns_token_invalid) return;
    try {
      let res = await send(device.apns_token, device.apns_env);
      if (res.reason === 'BadDeviceToken') {
        const other = device.apns_env === 'sandbox' ? 'production' : 'sandbox';
        res = await send(device.apns_token, other);
        if (res.status === 200) device.apns_env = other;
      }
      noteApnsResult(res, kind);
    } catch (err) {
      log(`${kind} push failed: ${err.message}`);
    }
  }

  function pushBackground(job) {
    return pushToDevice('background', (token, env) => apns.sendBackground(token, env, job.id));
  }

  function pushAlert(job) {
    return pushToDevice('alert', (token, env) => apns.sendAlert(token, env, {
      title: 'Muse has a Telegram job waiting',
      body: job.note ? `${job.note}. Open Telegram4Muse to run it.` : 'Open Telegram4Muse to run it.',
      jobId: job.id,
    }));
  }

  function noteApnsResult(res, kind) {
    const device = store.device;
    if (!device) return;
    device.last_push = { kind, at: now(), status: res.status, reason: res.reason };
    if (res.status === 410 || res.reason === 'BadDeviceToken' || res.reason === 'Unregistered') {
      device.apns_token_invalid = true;
    }
    if (res.status !== 200) log(`APNs ${kind} push: ${res.status} ${res.reason ?? ''}`);
    store.save();
  }

  // Moves jobs along: push when due, alert if the silent push didn't work,
  // expire stale jobs, and fail jobs whose run was never reported.
  let ticking = null;
  async function tick() {
    if (ticking) return ticking;
    ticking = (async () => {
      const t = now();
      let changed = false;
      const pushes = [];
      for (const job of store.jobs) {
        if (job.status === 'queued') {
          if (t >= job.expires_at) {
            Object.assign(job, { status: 'expired', finished_at: t, error: 'The phone did not pick up the job before it expired.' });
            changed = true;
          } else if (t >= job.run_at && !job.pushed_at) {
            job.pushed_at = t;
            changed = true;
            pushes.push(pushBackground(job));
          } else if (job.pushed_at && !job.alerted_at && t - job.pushed_at >= config.alertAfterMs) {
            job.alerted_at = t;
            changed = true;
            pushes.push(pushAlert(job));
          }
        } else if (job.status === 'running' && t - job.claimed_at >= config.leaseMs) {
          Object.assign(job, {
            status: 'failed',
            finished_at: t,
            error: 'lease_expired: the phone took the job but never reported a result. Check Telegram before retrying; some steps may have run.',
          });
          changed = true;
        }
      }
      if (store.prune(t)) changed = true;
      if (changed) store.save();
      await Promise.all(pushes);
    })();
    try {
      await ticking;
    } finally {
      ticking = null;
    }
  }

  function requireAgent(req) {
    if (!tokenMatches(req.headers.authorization, config.agentToken)) {
      throw new HttpError(401, 'unauthorized', 'missing or wrong agent token');
    }
  }

  function requireDevice(req) {
    if (!tokenMatches(req.headers.authorization, config.deviceToken)) {
      throw new HttpError(401, 'unauthorized', 'missing or wrong device token');
    }
    if (store.device) store.device.last_seen_at = now();
  }

  function getJob(id) {
    const job = store.findJob(id);
    if (!job) throw new HttpError(404, 'not_found', `no job ${id}`);
    return job;
  }

  const routes = [
    ['GET', /^\/healthz$/, async () => [200, { ok: true }]],

    // ----- agent -----
    ['GET', /^\/v1\/status$/, async (req) => {
      requireAgent(req);
      const d = store.device;
      const counts = {};
      for (const j of store.jobs) counts[j.status] = (counts[j.status] ?? 0) + 1;
      return [200, {
        device: d ? {
          registered_at: iso(d.registered_at),
          last_seen_at: iso(d.last_seen_at),
          app_version: d.app_version ?? null,
          push_ready: Boolean(apns && d.apns_token && !d.apns_token_invalid),
          last_push: d.last_push ? { ...d.last_push, at: iso(d.last_push.at) } : null,
        } : null,
        push_configured: Boolean(apns),
        allowed_chats: store.chats?.items.length ?? 0,
        jobs: counts,
      }];
    }],
    ['GET', /^\/v1\/chats$/, async (req) => {
      requireAgent(req);
      if (!store.chats) return [200, { chats: [], updated_at: null, hint: 'Open Telegram4Muse on the phone and choose the groups Muse may use.' }];
      return [200, { chats: store.chats.items, updated_at: iso(store.chats.updated_at) }];
    }],
    ['POST', /^\/v1\/jobs$/, async (req) => {
      requireAgent(req);
      const body = await readJson(req);
      const spec = normalizeJob(body, { now: now(), defaultExpiresAfterMs: config.defaultExpiresAfterMs });
      if (spec.idempotency_key) {
        const existing = store.findByIdempotencyKey(spec.idempotency_key);
        if (existing) return [200, present(existing)];
      }
      if (!store.chats) {
        throw new HttpError(409, 'no_allowed_chats', 'No groups are allowed yet. Open Telegram4Muse on the phone and choose groups first.');
      }
      const allowed = new Set(store.chats.items.map((c) => c.id));
      const denied = chatIdsOf(spec.steps).filter((id) => !allowed.has(id));
      if (denied.length) {
        throw new HttpError(403, 'chat_not_allowed', `chat ${denied.join(', ')} is not in the allowed list (see GET /v1/chats)`);
      }
      const job = {
        id: `job_${crypto.randomBytes(8).toString('hex')}`,
        status: 'queued',
        created_at: now(),
        ...spec,
      };
      store.addJob(job);
      store.save();
      await tick();
      return [201, present(job)];
    }],
    ['GET', /^\/v1\/jobs$/, async (req, _m, url) => {
      requireAgent(req);
      const limit = Math.min(Math.max(Number(url.searchParams.get('limit')) || 20, 1), 100);
      return [200, { jobs: store.jobs.slice(-limit).reverse().map(present) }];
    }],
    ['GET', /^\/v1\/jobs\/([\w-]+)$/, async (req, m) => {
      requireAgent(req);
      return [200, present(getJob(m[1]))];
    }],
    ['POST', /^\/v1\/jobs\/([\w-]+)\/cancel$/, async (req, m) => {
      requireAgent(req);
      const job = getJob(m[1]);
      if (job.status !== 'queued') {
        throw new HttpError(409, 'not_cancelable', `job is ${job.status}; only queued jobs can be canceled`);
      }
      Object.assign(job, { status: 'canceled', finished_at: now() });
      store.save();
      return [200, present(job)];
    }],

    // ----- device -----
    ['POST', /^\/v1\/device\/register$/, async (req) => {
      requireDevice(req);
      const body = await readJson(req);
      const token = body.apns_token;
      if (token !== undefined && token !== null && (typeof token !== 'string' || !/^[0-9a-fA-F]{32,200}$/.test(token))) {
        throw new HttpError(400, 'invalid_request', 'apns_token must be a hex string');
      }
      const env = body.apns_env === 'sandbox' ? 'sandbox' : 'production';
      const prev = store.device ?? {};
      store.device = {
        ...prev,
        apns_token: token ?? prev.apns_token ?? null,
        apns_env: env,
        apns_token_invalid: token ? false : prev.apns_token_invalid ?? false,
        app_version: typeof body.app_version === 'string' ? body.app_version.slice(0, 40) : prev.app_version ?? null,
        registered_at: prev.registered_at ?? now(),
        last_seen_at: now(),
      };
      store.save();
      return [200, { ok: true, push_configured: Boolean(apns) }];
    }],
    ['PUT', /^\/v1\/device\/chats$/, async (req) => {
      requireDevice(req);
      const items = normalizeChats(await readJson(req));
      store.chats = { items, updated_at: now() };
      store.save();
      return [200, { ok: true, count: items.length }];
    }],
    ['POST', /^\/v1\/device\/claim$/, async (req) => {
      requireDevice(req);
      await tick(); // expire anything stale before handing jobs out
      const t = now();
      const due = store.jobs
        .filter((j) => j.status === 'queued' && j.run_at <= t && t < j.expires_at)
        .slice(0, MAX_CLAIM);
      for (const job of due) Object.assign(job, { status: 'running', claimed_at: t });
      store.save();
      return [200, { jobs: due.map((j) => ({ id: j.id, note: j.note ?? null, steps: j.steps })) }];
    }],
    ['POST', /^\/v1\/device\/jobs\/([\w-]+)\/result$/, async (req, m) => {
      requireDevice(req);
      const job = getJob(m[1]);
      const result = normalizeResult(await readJson(req));
      const late = job.status === 'failed' && job.error?.startsWith('lease_expired');
      if (job.status !== 'running' && !late) {
        throw new HttpError(409, 'not_running', `job is ${job.status}`);
      }
      Object.assign(job, {
        status: result.status,
        finished_at: now(),
        result: { steps: result.steps },
        error: result.error,
      });
      store.save();
      return [200, { ok: true }];
    }],
  ];

  async function handle(req, res) {
    const url = new URL(req.url, 'http://bridge');
    let status = 500;
    let payload;
    try {
      const pathMatches = routes.filter(([, re]) => re.test(url.pathname));
      if (!pathMatches.length) throw new HttpError(404, 'not_found', 'no such endpoint');
      const route = pathMatches.find(([method]) => method === req.method);
      if (!route) throw new HttpError(405, 'method_not_allowed', `use ${pathMatches.map(([m]) => m).join(' or ')}`);
      [status, payload] = await route[2](req, url.pathname.match(route[1]), url);
    } catch (err) {
      if (err instanceof HttpError) {
        status = err.status;
        payload = { error: { code: err.code, message: err.message } };
      } else if (err instanceof ValidationError) {
        status = 400;
        payload = { error: { code: err.code, message: err.message } };
      } else {
        log(`internal error: ${err.stack ?? err}`);
        status = 500;
        payload = { error: { code: 'internal', message: 'internal error' } };
      }
    }
    const body = JSON.stringify(payload);
    res.writeHead(status, { 'content-type': 'application/json', 'cache-control': 'no-store' });
    res.end(body);
  }

  return { handle, tick };
}
