// A JSON-file store. This bridge serves one person and one phone, handling a
// few jobs a week, so a single file written atomically is plenty.

import fs from 'node:fs';
import path from 'node:path';

const KEEP_FINISHED_MS = 30 * 24 * 3600 * 1000;
const MAX_JOBS = 500;

function emptyState() {
  return { version: 1, device: null, chats: null, jobs: [] };
}

export class Store {
  constructor(file) {
    this.file = file;
    this.state = emptyState();
    if (file && fs.existsSync(file)) {
      this.state = { ...emptyState(), ...JSON.parse(fs.readFileSync(file, 'utf8')) };
    }
  }

  save() {
    if (!this.file) return;
    fs.mkdirSync(path.dirname(this.file), { recursive: true });
    const tmp = `${this.file}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(this.state, null, 2), { mode: 0o600 });
    fs.renameSync(tmp, this.file);
  }

  get device() {
    return this.state.device;
  }

  set device(value) {
    this.state.device = value;
  }

  get chats() {
    return this.state.chats;
  }

  set chats(value) {
    this.state.chats = value;
  }

  get jobs() {
    return this.state.jobs;
  }

  findJob(id) {
    return this.state.jobs.find((j) => j.id === id);
  }

  findByIdempotencyKey(key) {
    return this.state.jobs.find((j) => j.idempotency_key === key);
  }

  addJob(job) {
    this.state.jobs.push(job);
  }

  // Drop old finished jobs so the file stays small.
  prune(now) {
    const done = new Set(['succeeded', 'failed', 'canceled', 'expired']);
    let jobs = this.state.jobs.filter((j) => !(done.has(j.status) && now - (j.finished_at ?? j.created_at) > KEEP_FINISHED_MS));
    if (jobs.length > MAX_JOBS) {
      const active = jobs.filter((j) => !done.has(j.status));
      const finished = jobs.filter((j) => done.has(j.status)).slice(-(MAX_JOBS - active.length));
      jobs = [...active, ...finished].sort((a, b) => a.created_at - b.created_at);
    }
    const changed = jobs.length !== this.state.jobs.length;
    this.state.jobs = jobs;
    return changed;
  }
}
