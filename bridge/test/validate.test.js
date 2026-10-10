import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeJob, normalizeChats, ValidationError } from '../src/validate.js';

const NOW = Date.parse('2026-10-16T15:00:00Z');
const opts = { now: NOW, defaultExpiresAfterMs: 6 * 3600 * 1000 };
const CHAT = -1001234567890;

test('surf workflow: poll, pin it, reply to it', () => {
  const job = normalizeJob({
    chat_id: CHAT,
    note: 'Friday surf poll',
    steps: [
      { action: 'send_poll', question: 'Surf Saturday?', options: ['Dawn patrol', 'Midday', 'Out'] },
      { action: 'pin_message', message: { step: 0 } },
      { action: 'send_message', text: 'Vote above!', reply_to: { step: 0 } },
    ],
  }, opts);
  assert.equal(job.steps.length, 3);
  assert.deepEqual(job.steps[0], {
    action: 'send_poll',
    chat_id: CHAT,
    question: 'Surf Saturday?',
    options: ['Dawn patrol', 'Midday', 'Out'],
    is_anonymous: false,
    allows_multiple_answers: false,
    allows_revoting: false,
    silent: false,
  });
  assert.deepEqual(job.steps[1], { action: 'pin_message', chat_id: CHAT, message: { step: 0 }, notify: false });
  assert.deepEqual(job.steps[2].reply_to, { step: 0 });
  assert.equal(job.run_at, NOW);
  assert.equal(job.expires_at, NOW + 6 * 3600 * 1000);
  assert.equal(job.note, 'Friday surf poll');
});

test('chat id may be a numeric string and step-level chat overrides job-level', () => {
  const job = normalizeJob({
    chat_id: String(CHAT),
    steps: [
      { action: 'send_message', text: 'hi' },
      { action: 'send_message', chat_id: -42, text: 'hello' },
    ],
  }, opts);
  assert.equal(job.steps[0].chat_id, CHAT);
  assert.equal(job.steps[1].chat_id, -42);
});

test('bare number is a message_id reference', () => {
  const job = normalizeJob({ chat_id: CHAT, steps: [{ action: 'get_poll_results', message: 12345 }] }, opts);
  assert.deepEqual(job.steps[0].message, { message_id: 12345 });
  assert.equal(job.steps[0].include_voters, true);
});

test('run_at and expires_at', () => {
  const job = normalizeJob({
    chat_id: CHAT,
    run_at: '2026-10-17T08:00:00-07:00',
    steps: [{ action: 'send_message', text: 'morning' }],
  }, opts);
  assert.equal(job.run_at, Date.parse('2026-10-17T15:00:00Z'));
  assert.equal(job.expires_at, job.run_at + 6 * 3600 * 1000);
});

const bad = [
  ['missing steps', {}, /steps must be a non-empty array/],
  ['unknown field', { steps: [], foo: 1 }, /unknown field "foo"/],
  ['no chat', { steps: [{ action: 'send_message', text: 'x' }] }, /chat_id is required/],
  ['bad action', { chat_id: CHAT, steps: [{ action: 'delete_chat' }] }, /action must be one of/],
  ['one option', { chat_id: CHAT, steps: [{ action: 'send_poll', question: 'q', options: ['a'] }] }, /2-12 entries/],
  ['empty option', { chat_id: CHAT, steps: [{ action: 'send_poll', question: 'q', options: ['a', ' '] }] }, /options\[1\] must not be empty/],
  ['long question', { chat_id: CHAT, steps: [{ action: 'send_poll', question: 'x'.repeat(256), options: ['a', 'b'] }] }, /at most 255/],
  ['forward ref', { chat_id: CHAT, steps: [{ action: 'pin_message', message: { step: 0 } }] }, /earlier step/],
  ['ref to non-message step', {
    chat_id: CHAT,
    steps: [
      { action: 'send_message', text: 'a' },
      { action: 'pin_message', message: { step: 0 } },
      { action: 'stop_poll', message: { step: 1 } },
    ],
  }, /does not create a message/],
  ['ref across chats', {
    steps: [
      { action: 'send_message', chat_id: 1, text: 'a' },
      { action: 'pin_message', chat_id: 2, message: { step: 0 } },
    ],
  }, /different chat/],
  ['both ref kinds', { chat_id: CHAT, steps: [{ action: 'pin_message', message: { step: 0, message_id: 1 } }] }, /exactly one/],
  ['zero chat', { chat_id: 0, steps: [{ action: 'send_message', text: 'a' }] }, /non-zero integer/],
  ['past expiry', { chat_id: CHAT, expires_at: '2020-01-01T00:00:00Z', steps: [{ action: 'send_message', text: 'a' }] }, /in the past/],
  ['bad date', { chat_id: CHAT, run_at: 'friday', steps: [{ action: 'send_message', text: 'a' }] }, /ISO-8601/],
  ['too many steps', { chat_id: CHAT, steps: Array.from({ length: 11 }, () => ({ action: 'send_message', text: 'a' })) }, /at most 10/],
  ['non-bool', { chat_id: CHAT, steps: [{ action: 'send_message', text: 'a', silent: 'yes' }] }, /true or false/],
];

for (const [name, body, pattern] of bad) {
  test(`rejects: ${name}`, () => {
    assert.throws(() => normalizeJob(body, opts), (err) => err instanceof ValidationError && pattern.test(err.message));
  });
}

test('normalizeChats', () => {
  assert.deepEqual(normalizeChats({ chats: [{ id: '-100', title: 'Surf', type: 'supergroup' }] }), [
    { id: -100, title: 'Surf', type: 'supergroup' },
  ]);
  assert.throws(() => normalizeChats({ chats: [{ id: 'abc' }] }), ValidationError);
});
