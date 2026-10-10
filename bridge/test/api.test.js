import test from 'node:test';
import assert from 'node:assert/strict';
import crypto from 'node:crypto';
import fs from 'node:fs';
import http from 'node:http';
import http2 from 'node:http2';
import os from 'node:os';
import path from 'node:path';
import { createApp } from '../src/app.js';
import { ApnsClient } from '../src/apns.js';
import { loadConfig } from '../src/config.js';
import { Store } from '../src/store.js';

const AGENT = 'agent-token-0123456789abcdef0123';
const DEVICE = 'device-token-0123456789abcdef012';
const CHAT = -1001234567890;
const APNS_TOKEN = 'ab'.repeat(32);
const MIN = 60 * 1000;

// A stand-in for Apple's push service (cleartext HTTP/2) that checks the
// provider token signature and records what it received.
async function fakeApns(publicKey, { status = 200, reason, respond } = {}) {
  const received = [];
  const server = http2.createServer();
  server.on('stream', (stream, headers) => {
    const chunks = [];
    stream.on('data', (c) => chunks.push(c));
    stream.on('end', () => {
      const [h, c, s] = headers.authorization.replace('bearer ', '').split('.');
      const validJwt = crypto.verify(
        'sha256',
        Buffer.from(`${h}.${c}`),
        { key: publicKey, dsaEncoding: 'ieee-p1363' },
        Buffer.from(s, 'base64url'),
      );
      received.push({
        path: headers[':path'],
        pushType: headers['apns-push-type'],
        priority: headers['apns-priority'],
        topic: headers['apns-topic'],
        jwtHeader: JSON.parse(Buffer.from(h, 'base64url')),
        jwtClaims: JSON.parse(Buffer.from(c, 'base64url')),
        validJwt,
        body: JSON.parse(Buffer.concat(chunks).toString()),
      });
      const reply = respond ? respond(received) : { status, reason };
      stream.respond({ ':status': reply.status, 'apns-id': 'test-id' });
      stream.end(reply.reason ? JSON.stringify({ reason: reply.reason }) : '');
    });
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  return { received, url: `http://127.0.0.1:${server.address().port}`, close: () => server.close() };
}

async function setup(t, { apnsStatus, apnsReason, apnsRespond } = {}) {
  const { privateKey, publicKey } = crypto.generateKeyPairSync('ec', { namedCurve: 'prime256v1' });
  const apnsServer = await fakeApns(publicKey, { status: apnsStatus, reason: apnsReason, respond: apnsRespond });
  let clock = Date.parse('2026-10-16T15:00:00Z');
  const now = () => clock;
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 't4m-'));
  const dataFile = path.join(dir, 'state.json');
  const config = loadConfig({ AGENT_TOKEN: AGENT, DEVICE_TOKEN: DEVICE, ALERT_AFTER_SECONDS: '600' });
  const apns = new ApnsClient({
    keyId: 'KEY123',
    teamId: 'TEAM123',
    topic: 'com.example.telegram4muse',
    privateKey: privateKey.export({ type: 'pkcs8', format: 'pem' }),
    host: apnsServer.url,
    now,
  });
  const store = new Store(dataFile);
  const app = createApp({ config, store, apns, now });
  const server = http.createServer(app.handle);
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const base = `http://127.0.0.1:${server.address().port}`;
  t.after(() => {
    server.close();
    apns.close();
    apnsServer.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });

  async function call(method, url, { token, body } = {}) {
    const res = await fetch(base + url, {
      method,
      headers: { ...(token && { authorization: `Bearer ${token}` }), 'content-type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    return { status: res.status, body: await res.json() };
  }
  const agent = (method, url, body) => call(method, url, { token: AGENT, body });
  const device = (method, url, body) => call(method, url, { token: DEVICE, body });
  return {
    app, call, agent, device, dataFile,
    apns: apnsServer,
    advance: (ms) => { clock += ms; },
    now,
  };
}

const surfJob = {
  chat_id: CHAT,
  note: 'Friday surf poll',
  idempotency_key: 'surf-2026-10-16',
  steps: [
    { action: 'send_poll', question: 'Surf Saturday?', options: ['Dawn patrol', 'Midday', 'Out'] },
    { action: 'pin_message', message: { step: 0 } },
  ],
};

async function pairPhone(s) {
  assert.equal((await s.device('POST', '/v1/device/register', { apns_token: APNS_TOKEN, apns_env: 'sandbox', app_version: '1.0' })).status, 200);
  assert.equal((await s.device('PUT', '/v1/device/chats', { chats: [{ id: CHAT, title: 'Surf Crew', type: 'supergroup' }] })).status, 200);
}

test('auth: each side needs its own token', async (t) => {
  const s = await setup(t);
  assert.equal((await s.call('GET', '/healthz')).status, 200);
  assert.equal((await s.call('GET', '/v1/status')).status, 401);
  assert.equal((await s.device('GET', '/v1/status')).status, 401);
  assert.equal((await s.agent('POST', '/v1/device/claim')).status, 401);
  assert.equal((await s.agent('GET', '/v1/status')).status, 200);
  assert.equal((await s.agent('GET', '/v1/nope')).status, 404);
  assert.equal((await s.agent('DELETE', '/v1/status')).status, 405);
});

test('no jobs until the phone has published allowed groups', async (t) => {
  const s = await setup(t);
  const res = await s.agent('POST', '/v1/jobs', surfJob);
  assert.equal(res.status, 409);
  assert.equal(res.body.error.code, 'no_allowed_chats');
});

test('jobs for chats outside the allowlist are refused', async (t) => {
  const s = await setup(t);
  await pairPhone(s);
  const res = await s.agent('POST', '/v1/jobs', { ...surfJob, chat_id: -100999 });
  assert.equal(res.status, 403);
  assert.equal(res.body.error.code, 'chat_not_allowed');
});

test('validation errors come back as 400 with a message', async (t) => {
  const s = await setup(t);
  await pairPhone(s);
  const res = await s.agent('POST', '/v1/jobs', { chat_id: CHAT, steps: [{ action: 'send_poll', question: 'q', options: ['a'] }] });
  assert.equal(res.status, 400);
  assert.match(res.body.error.message, /2-12 entries/);
});

test('full round trip: agent -> push -> phone -> result -> agent', async (t) => {
  const s = await setup(t);
  await pairPhone(s);

  const chats = await s.agent('GET', '/v1/chats');
  assert.deepEqual(chats.body.chats, [{ id: CHAT, title: 'Surf Crew', type: 'supergroup' }]);

  const created = await s.agent('POST', '/v1/jobs', surfJob);
  assert.equal(created.status, 201);
  assert.equal(created.body.status, 'queued');
  const id = created.body.id;

  // The phone got a silent push signed with a valid provider token.
  assert.equal(s.apns.received.length, 1);
  const push = s.apns.received[0];
  assert.equal(push.path, `/3/device/${APNS_TOKEN}`);
  assert.equal(push.pushType, 'background');
  assert.equal(push.priority, '5');
  assert.equal(push.topic, 'com.example.telegram4muse');
  assert.deepEqual(push.body, { aps: { 'content-available': 1 }, t4m: { job: id } });
  assert.equal(push.validJwt, true);
  assert.deepEqual(push.jwtHeader, { alg: 'ES256', kid: 'KEY123' });
  assert.equal(push.jwtClaims.iss, 'TEAM123');

  // Retrying the same request returns the same job instead of a duplicate.
  const again = await s.agent('POST', '/v1/jobs', surfJob);
  assert.equal(again.status, 200);
  assert.equal(again.body.id, id);

  // The phone claims it; a second claim gets nothing.
  const claim = await s.device('POST', '/v1/device/claim');
  assert.equal(claim.body.jobs.length, 1);
  assert.equal(claim.body.jobs[0].id, id);
  assert.equal(claim.body.jobs[0].steps[0].chat_id, CHAT);
  assert.equal((await s.device('POST', '/v1/device/claim')).body.jobs.length, 0);
  assert.equal((await s.agent('GET', `/v1/jobs/${id}`)).body.status, 'running');

  // Canceling a running job isn't possible.
  assert.equal((await s.agent('POST', `/v1/jobs/${id}/cancel`)).status, 409);

  const steps = [
    { index: 0, ok: true, result: { message_id: 1048576, chat_id: CHAT } },
    { index: 1, ok: true, result: {} },
  ];
  assert.equal((await s.device('POST', `/v1/device/jobs/${id}/result`, { status: 'succeeded', steps })).status, 200);
  // Results are accepted once.
  assert.equal((await s.device('POST', `/v1/device/jobs/${id}/result`, { status: 'succeeded', steps })).status, 409);

  const done = await s.agent('GET', `/v1/jobs/${id}`);
  assert.equal(done.body.status, 'succeeded');
  assert.deepEqual(done.body.result.steps, steps);

  const status = await s.agent('GET', '/v1/status');
  assert.equal(status.body.device.push_ready, true);
  assert.equal(status.body.allowed_chats, 1);
  assert.deepEqual(status.body.jobs, { succeeded: 1 });

  const list = await s.agent('GET', '/v1/jobs?limit=5');
  assert.equal(list.body.jobs.length, 1);

  // State survives a restart.
  const reloaded = new Store(s.dataFile);
  assert.equal(reloaded.findJob(id).status, 'succeeded');
});

test('scheduled job waits for run_at, then alerts if the silent push did nothing, then expires', async (t) => {
  const s = await setup(t);
  await pairPhone(s);
  const runAt = new Date(s.now() + 60 * MIN).toISOString();
  const created = await s.agent('POST', '/v1/jobs', { ...surfJob, idempotency_key: undefined, run_at: runAt });
  assert.equal(created.status, 201);
  assert.equal(s.apns.received.length, 0);
  assert.equal((await s.device('POST', '/v1/device/claim')).body.jobs.length, 0, 'not due yet');

  s.advance(60 * MIN);
  await s.app.tick();
  assert.equal(s.apns.received.length, 1);
  assert.equal(s.apns.received[0].pushType, 'background');

  s.advance(10 * MIN);
  await s.app.tick();
  assert.equal(s.apns.received.length, 2);
  assert.equal(s.apns.received[1].pushType, 'alert');
  assert.equal(s.apns.received[1].priority, '10');
  assert.match(s.apns.received[1].body.aps.alert.body, /Friday surf poll/);

  s.advance(6 * 60 * MIN);
  await s.app.tick();
  const job = await s.agent('GET', `/v1/jobs/${created.body.id}`);
  assert.equal(job.body.status, 'expired');
  assert.equal((await s.device('POST', '/v1/device/claim')).body.jobs.length, 0);
});

test('a claimed job that never reports back fails, and a late report still lands', async (t) => {
  const s = await setup(t);
  await pairPhone(s);
  const { body } = await s.agent('POST', '/v1/jobs', surfJob);
  await s.device('POST', '/v1/device/claim');
  s.advance(5 * MIN);
  await s.app.tick();
  const failed = await s.agent('GET', `/v1/jobs/${body.id}`);
  assert.equal(failed.body.status, 'failed');
  assert.match(failed.body.error, /^lease_expired/);

  const late = await s.device('POST', `/v1/device/jobs/${body.id}/result`, { status: 'failed', steps: [], error: 'Chat not found' });
  assert.equal(late.status, 200);
  assert.equal((await s.agent('GET', `/v1/jobs/${body.id}`)).body.error, 'Chat not found');
});

test('queued jobs can be canceled', async (t) => {
  const s = await setup(t);
  await pairPhone(s);
  const { body } = await s.agent('POST', '/v1/jobs', { ...surfJob, run_at: new Date(s.now() + 60 * MIN).toISOString() });
  const res = await s.agent('POST', `/v1/jobs/${body.id}/cancel`);
  assert.equal(res.body.status, 'canceled');
  s.advance(60 * MIN);
  await s.app.tick();
  assert.equal(s.apns.received.length, 0);
});

test('an unregistered device token is remembered so we stop pushing to it', async (t) => {
  const s = await setup(t, { apnsStatus: 410, apnsReason: 'Unregistered' });
  await pairPhone(s);
  await s.agent('POST', '/v1/jobs', surfJob);
  const status = await s.agent('GET', '/v1/status');
  assert.equal(status.body.device.push_ready, false);
  assert.equal(status.body.device.last_push.reason, 'Unregistered');
  // Re-registering with a fresh token makes it usable again.
  await s.device('POST', '/v1/device/register', { apns_token: 'cd'.repeat(32), apns_env: 'sandbox' });
  assert.equal((await s.agent('GET', '/v1/status')).body.device.push_ready, true);
});

test('a token from the other APNs environment is retried there and remembered', async (t) => {
  // First attempt (sandbox, as registered) is rejected; the production retry works.
  const s = await setup(t, { apnsRespond: (received) => (received.length === 1 ? { status: 400, reason: 'BadDeviceToken' } : { status: 200 }) });
  await pairPhone(s);
  await s.agent('POST', '/v1/jobs', surfJob);
  assert.equal(s.apns.received.length, 2);
  const status = await s.agent('GET', '/v1/status');
  assert.equal(status.body.device.push_ready, true);
  assert.equal(status.body.device.last_push.status, 200);
  const saved = JSON.parse(fs.readFileSync(s.dataFile, 'utf8'));
  assert.equal(saved.device.apns_env, 'production');
});

test('config refuses weak or reused tokens', () => {
  assert.throws(() => loadConfig({ AGENT_TOKEN: 'short', DEVICE_TOKEN: DEVICE }), /too short/);
  assert.throws(() => loadConfig({ AGENT_TOKEN: AGENT, DEVICE_TOKEN: AGENT }), /must be different/);
  assert.throws(() => loadConfig({ AGENT_TOKEN: AGENT }), /DEVICE_TOKEN is not set/);
});

// The iPhone app's tests decode the same files (ios/T4MCore/Tests/T4MCoreTests/Fixtures),
// so these two tests keep the bridge and the app speaking the same format.
const FIXTURES = new URL('../../ios/T4MCore/Tests/T4MCoreTests/Fixtures/', import.meta.url);

test('contract: claim response matches the fixture the app decodes', async (t) => {
  const s = await setup(t);
  await pairPhone(s);
  const created = await s.agent('POST', '/v1/jobs', {
    chat_id: CHAT,
    note: 'Friday surf poll',
    steps: [
      { action: 'send_poll', question: 'Surf Saturday?', options: ['Dawn patrol', 'Midday', 'Out'] },
      { action: 'pin_message', message: { step: 0 } },
      { action: 'send_message', text: 'Vote above! 🏄', reply_to: { step: 0 }, silent: true },
      { action: 'get_poll_results', message: { message_id: 1048576 } },
    ],
  });
  assert.equal(created.status, 201);
  const claim = await s.device('POST', '/v1/device/claim');
  claim.body.jobs[0].id = 'job_fixture';
  const fixture = JSON.parse(fs.readFileSync(new URL('claim-response.json', FIXTURES), 'utf8'));
  assert.deepEqual(claim.body, fixture);
});

test('contract: the report the app sends is accepted and stored as-is', async (t) => {
  const s = await setup(t);
  await pairPhone(s);
  const { body } = await s.agent('POST', '/v1/jobs', surfJob);
  await s.device('POST', '/v1/device/claim');
  const report = JSON.parse(fs.readFileSync(new URL('job-report.json', FIXTURES), 'utf8'));
  assert.equal((await s.device('POST', `/v1/device/jobs/${body.id}/result`, report)).status, 200);
  const job = await s.agent('GET', `/v1/jobs/${body.id}`);
  assert.equal(job.body.status, 'succeeded');
  assert.deepEqual(job.body.result.steps, report.steps);
  assert.equal(job.body.error, null);
});
