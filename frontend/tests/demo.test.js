// Device/provider-free tests of the actual page module's lifecycle callbacks.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

let moduleId = 0;

class Element {
  constructor() {
    this.handlers = {};
    this.children = [];
    this.hidden = false;
    this.textContent = '';
    this.classList = { remove() {} };
  }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  append(...elements) { for (const e of elements) { e.parentElement = this; this.children.push(e); } }
  replaceChildren() { this.children = []; }
  remove() {}
  setAttribute(name, value) { this[name] = value; }
  pause() {}
  play() { return Promise.resolve(); }
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

async function page({ denied = false } = {}) {
  const ids = ['start', 'mute', 'end', 'bot-audio', 'mic-level', 'transcript', 'status', 'error', 'empty', 'audio-enable', 'history', 'history-link'];
  const elements = Object.fromEntries(ids.map((id) => [id, new Element()]));
  globalThis.document = { getElementById: (id) => elements[id], createElement: () => new Element() };
  globalThis.window = { addEventListener() {} };
  globalThis.MediaStream = class {};
  let current;
  globalThis.PipecatClient = class {
    constructor(options) { current = this; this.callbacks = options.callbacks; this.micChanges = []; this.sent = []; }
    async initDevices() { if (denied) throw new Error('Microphone permission denied'); }
    enableMic(enabled) { this.micChanges.push(enabled); }
    async connect() {
      this.callbacks.onTrackStarted({ kind: 'audio' }, { local: false });
      this.callbacks.onBotReady();
      this.callbacks.onServerMessage({ type: 'session', call_sid: `browser_${'a'.repeat(32)}` });
    }
    sendClientMessage(type) { this.sent.push(type); }
    async disconnect() { this.disconnected = true; this.callbacks.onDisconnected(); }
  };
  globalThis.SmallWebRTCTransport = class {};
  const source = (await readFile(new URL('../demo.js', import.meta.url), 'utf8'))
    .replace(/^import .*;\r?\n/gm, '');
  await import(`data:text/javascript;base64,${Buffer.from(`${source}\n// case ${moduleId++}`).toString('base64')}`);
  return { elements, client: () => current };
}

test('input stays muted until greeting completion and readiness is sent once', async () => {
  const { elements, client } = await page();
  await elements.start.handlers.click();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
  assert.deepEqual(client().micChanges, [false]);
  client().callbacks.onBotReady();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
  client().callbacks.onBotStartedSpeaking();
  client().callbacks.onBotStoppedSpeaking();
  assert.deepEqual(client().micChanges, [false, true]);
  assert.equal(elements.status.textContent, 'Listening…');
});

test('mute chosen during greeting is respected after speech', async () => {
  const { elements, client } = await page();
  await elements.start.handlers.click();
  elements.mute.handlers.click();
  client().callbacks.onBotStartedSpeaking();
  client().callbacks.onBotStoppedSpeaking();
  assert.equal(client().micChanges.at(-1), false);
  assert.equal(elements.mute.textContent, 'Unmute');
});

test('blocked playback provides an explicit recovery action before greeting', async () => {
  const { elements, client } = await page();
  elements['bot-audio'].play = () => Promise.reject(new Error('autoplay blocked'));
  await elements.start.handlers.click();
  await settle();
  assert.deepEqual(client().sent, []);
  assert.equal(elements['audio-enable'].hidden, false);
  elements['bot-audio'].play = () => Promise.resolve();
  elements['audio-enable'].handlers.click();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
});

test('End waits for the saved response, disconnects, and ignores late callbacks', async () => {
  const { elements, client } = await page();
  let complete;
  globalThis.fetch = () => new Promise((resolve) => { complete = resolve; });
  await elements.start.handlers.click();
  await settle();
  elements.end.handlers.click();
  assert.equal(elements.start.disabled, true);
  complete({ ok: true, json: async () => ({ saved: true, history_url: '/calls/saved' }) });
  await settle();
  assert.equal(client().disconnected, true);
  assert.equal(elements.start.disabled, false);
  assert.equal(elements.history.hidden, false);
  client().callbacks.onBotStartedSpeaking();
  assert.equal(elements.status.textContent, 'Conversation ended.');
});

test('denied microphone resets controls and displays a useful error', async () => {
  const { elements, client } = await page({ denied: true });
  await elements.start.handlers.click();
  assert.equal(elements.start.disabled, false);
  assert.equal(client().disconnected, true);
  assert.match(elements.error.textContent, /Microphone permission denied/);
});

test('overlapping playback callbacks send readiness only once', async () => {
  const { elements, client } = await page();
  const pending = [];
  elements['bot-audio'].play = () => new Promise((resolve) => pending.push(resolve));
  await elements.start.handlers.click();
  client().callbacks.onBotReady();
  for (const resolve of pending) resolve();
  await settle();
  assert.deepEqual(client().sent, ['ui-ready']);
});
