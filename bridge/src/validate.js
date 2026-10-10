// Validation and normalization of jobs submitted by the agent.
//
// A job is a short list of steps the phone runs in order, e.g.
//   send_poll -> pin_message {step: 0} -> send_message reply_to {step: 0}
// Steps can refer to messages created by earlier steps with {"step": <index>}.

export const ACTIONS = new Set([
  'send_poll',
  'send_message',
  'pin_message',
  'unpin_message',
  'stop_poll',
  'get_poll_results',
]);

// Actions whose result contains a new message that later steps can reference.
const PRODUCES_MESSAGE = new Set(['send_poll', 'send_message']);

export const LIMITS = {
  maxSteps: 10,
  pollQuestion: 255,
  pollOptionsMin: 2,
  pollOptionsMax: 12,
  pollOption: 100,
  text: 4096,
  note: 500,
  idempotencyKey: 200,
};

export class ValidationError extends Error {
  constructor(message, code = 'invalid_request') {
    super(message);
    this.code = code;
  }
}

function fail(message) {
  throw new ValidationError(message);
}

function isPlainObject(v) {
  return v !== null && typeof v === 'object' && !Array.isArray(v);
}

export function parseChatId(value, where) {
  let n = value;
  if (typeof value === 'string' && /^-?\d+$/.test(value.trim())) n = Number(value.trim());
  if (typeof n !== 'number' || !Number.isSafeInteger(n) || n === 0) {
    fail(`${where}: chat_id must be a non-zero integer`);
  }
  return n;
}

function str(value, where, max, { required = true } = {}) {
  if (value === undefined || value === null) {
    if (required) fail(`${where} is required`);
    return undefined;
  }
  if (typeof value !== 'string') fail(`${where} must be a string`);
  const s = value.trim();
  if (required && s.length === 0) fail(`${where} must not be empty`);
  if ([...s].length > max) fail(`${where} must be at most ${max} characters`);
  return s;
}

function bool(value, where, fallback) {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== 'boolean') fail(`${where} must be true or false`);
  return value;
}

// A reference to a message: {"step": i} (a message created earlier in this job)
// or {"message_id": n} (an id returned by a previous job). A bare number is
// shorthand for {"message_id": n}.
function messageRef(value, where, index, steps, chatId) {
  if (typeof value === 'number') value = { message_id: value };
  if (!isPlainObject(value)) fail(`${where} must be {"step": n} or {"message_id": n}`);
  const hasStep = value.step !== undefined;
  const hasId = value.message_id !== undefined;
  if (hasStep === hasId) fail(`${where} must have exactly one of "step" or "message_id"`);
  if (hasStep) {
    const s = value.step;
    if (!Number.isInteger(s) || s < 0 || s >= index) {
      fail(`${where}.step must refer to an earlier step (0..${index - 1})`);
    }
    if (!PRODUCES_MESSAGE.has(steps[s].action)) {
      fail(`${where}.step ${s} is a ${steps[s].action} step, which does not create a message`);
    }
    if (steps[s].chat_id !== chatId) fail(`${where}.step ${s} is in a different chat`);
    return { step: s };
  }
  const id = value.message_id;
  if (!Number.isSafeInteger(id) || id <= 0) fail(`${where}.message_id must be a positive integer`);
  return { message_id: id };
}

function normalizeStep(raw, index, steps, defaultChatId) {
  const where = `steps[${index}]`;
  if (!isPlainObject(raw)) fail(`${where} must be an object`);
  const action = raw.action;
  if (!ACTIONS.has(action)) {
    fail(`${where}.action must be one of: ${[...ACTIONS].join(', ')}`);
  }
  const rawChat = raw.chat_id ?? defaultChatId;
  if (rawChat === undefined) fail(`${where}.chat_id is required (or set chat_id on the job)`);
  const chat_id = parseChatId(rawChat, where);

  switch (action) {
    case 'send_poll': {
      const question = str(raw.question, `${where}.question`, LIMITS.pollQuestion);
      if (!Array.isArray(raw.options)) fail(`${where}.options must be an array of strings`);
      if (raw.options.length < LIMITS.pollOptionsMin || raw.options.length > LIMITS.pollOptionsMax) {
        fail(`${where}.options must have ${LIMITS.pollOptionsMin}-${LIMITS.pollOptionsMax} entries`);
      }
      const options = raw.options.map((o, i) => str(o, `${where}.options[${i}]`, LIMITS.pollOption));
      return {
        action,
        chat_id,
        question,
        options,
        is_anonymous: bool(raw.is_anonymous, `${where}.is_anonymous`, false),
        allows_multiple_answers: bool(raw.allows_multiple_answers, `${where}.allows_multiple_answers`, false),
        allows_revoting: bool(raw.allows_revoting, `${where}.allows_revoting`, false),
        silent: bool(raw.silent, `${where}.silent`, false),
        ...(raw.reply_to !== undefined && {
          reply_to: messageRef(raw.reply_to, `${where}.reply_to`, index, steps, chat_id),
        }),
      };
    }
    case 'send_message': {
      return {
        action,
        chat_id,
        text: str(raw.text, `${where}.text`, LIMITS.text),
        silent: bool(raw.silent, `${where}.silent`, false),
        ...(raw.reply_to !== undefined && {
          reply_to: messageRef(raw.reply_to, `${where}.reply_to`, index, steps, chat_id),
        }),
      };
    }
    case 'pin_message':
      return {
        action,
        chat_id,
        message: messageRef(raw.message, `${where}.message`, index, steps, chat_id),
        notify: bool(raw.notify, `${where}.notify`, false),
      };
    case 'unpin_message':
    case 'stop_poll':
      return { action, chat_id, message: messageRef(raw.message, `${where}.message`, index, steps, chat_id) };
    case 'get_poll_results':
      return {
        action,
        chat_id,
        message: messageRef(raw.message, `${where}.message`, index, steps, chat_id),
        include_voters: bool(raw.include_voters, `${where}.include_voters`, true),
      };
  }
  /* c8 ignore next */
  fail(`${where}: unsupported action`);
}

function parseTime(value, where) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'number' && typeof value !== 'string') fail(`${where} must be an ISO-8601 string`);
  // Numbers are Unix timestamps in seconds.
  const t = typeof value === 'number' ? value * 1000 : Date.parse(value);
  if (!Number.isFinite(t)) fail(`${where} must be an ISO-8601 timestamp, e.g. 2026-10-16T08:00:00-07:00`);
  return t;
}

// Returns a normalized job spec. Throws ValidationError on bad input.
// `now` is in ms; `defaultExpiresAfterMs` bounds how late a job may still run.
export function normalizeJob(body, { now, defaultExpiresAfterMs }) {
  if (!isPlainObject(body)) fail('body must be a JSON object');
  const known = new Set(['chat_id', 'steps', 'run_at', 'expires_at', 'note', 'idempotency_key']);
  for (const key of Object.keys(body)) {
    if (!known.has(key)) fail(`unknown field "${key}"`);
  }
  if (!Array.isArray(body.steps) || body.steps.length === 0) fail('steps must be a non-empty array');
  if (body.steps.length > LIMITS.maxSteps) fail(`at most ${LIMITS.maxSteps} steps per job`);

  const steps = [];
  body.steps.forEach((raw, i) => steps.push(normalizeStep(raw, i, steps, body.chat_id)));

  const runAt = parseTime(body.run_at, 'run_at') ?? now;
  const expiresAt = parseTime(body.expires_at, 'expires_at') ?? runAt + defaultExpiresAfterMs;
  if (expiresAt <= now) fail('expires_at is in the past');
  if (expiresAt <= runAt) fail('expires_at must be after run_at');

  return {
    steps,
    run_at: runAt,
    expires_at: expiresAt,
    note: str(body.note, 'note', LIMITS.note, { required: false }),
    idempotency_key: str(body.idempotency_key, 'idempotency_key', LIMITS.idempotencyKey, { required: false }),
  };
}

export function chatIdsOf(steps) {
  return [...new Set(steps.map((s) => s.chat_id))];
}

// The phone publishes the chats the user allowed. Validate that list.
export function normalizeChats(body) {
  if (!isPlainObject(body) || !Array.isArray(body.chats)) fail('body must be {"chats": [...]}');
  if (body.chats.length > 200) fail('too many chats');
  return body.chats.map((c, i) => {
    if (!isPlainObject(c)) fail(`chats[${i}] must be an object`);
    return {
      id: parseChatId(c.id, `chats[${i}]`),
      title: str(c.title, `chats[${i}].title`, 256, { required: false }) ?? '',
      type: str(c.type, `chats[${i}].type`, 32, { required: false }) ?? 'group',
    };
  });
}

// Result reported by the phone after running a job.
export function normalizeResult(body) {
  if (!isPlainObject(body)) fail('body must be a JSON object');
  if (body.status !== 'succeeded' && body.status !== 'failed') fail('status must be "succeeded" or "failed"');
  if (!Array.isArray(body.steps)) fail('steps must be an array');
  const json = JSON.stringify(body.steps);
  if (json.length > 200_000) fail('steps result too large');
  return {
    status: body.status,
    steps: body.steps,
    error: body.error === undefined || body.error === null ? null : String(body.error).slice(0, 2000),
  };
}
