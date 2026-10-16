import { PipecatClient } from '@pipecat-ai/client-js';
import { SmallWebRTCTransport } from '@pipecat-ai/small-webrtc-transport';

const element = (id) => /** @type {HTMLElement} */ (document.getElementById(id));
const startButton = /** @type {HTMLButtonElement} */ (element('start'));
const muteButton = /** @type {HTMLButtonElement} */ (element('mute'));
const endButton = /** @type {HTMLButtonElement} */ (element('end'));
const audio = /** @type {HTMLAudioElement} */ (element('bot-audio'));
const meter = /** @type {HTMLMeterElement} */ (element('mic-level'));
const transcript = element('transcript');
let client = null;
let generation = 0;
let callSid = null;
let muted = false;
let botReady = false;
let uiReady = false;
let greeting = true;
let botSpeaking = false;
let stopping = false;
let assistantRow = null;
let userDraft = null;

function status(text) { element('status').textContent = text; }
function error(text) {
  element('error').textContent = text;
  element('error').hidden = !text;
}

function row(role, text, draft = false) {
  element('empty')?.remove();
  const container = document.createElement('div');
  container.className = `utterance${draft ? ' draft' : ''}`;
  const label = document.createElement('strong');
  label.textContent = role;
  const body = document.createElement('span');
  body.textContent = text;
  container.append(label, body);
  transcript.append(container);
  transcript.scrollTop = transcript.scrollHeight;
  return body;
}

async function playbackReady() {
  if (!client || !botReady || !audio.srcObject || uiReady || stopping) return;
  const active = client;
  const current = generation;
  try {
    await audio.play();
    if (client !== active || generation !== current || uiReady || stopping) return;
    uiReady = true;
    element('audio-enable').hidden = true;
    client.sendClientMessage('ui-ready', {});
    status('Preparing your greeting…');
  } catch {
    if (client !== active || generation !== current || stopping) return;
    element('audio-enable').hidden = false;
    status('Click Enable audio to hear the agent.');
  }
}

function resetControls() {
  startButton.disabled = false;
  muteButton.disabled = true;
  endButton.disabled = true;
  meter.value = 0;
  element('audio-enable').hidden = true;
  audio.pause();
  audio.srcObject = null;
}

async function stop() {
  if (stopping || !client) return;
  stopping = true;
  const active = client;
  active.enableMic(false);
  endButton.disabled = true;
  muteButton.disabled = true;
  status('Ending and saving…');
  try {
    if (callSid) {
      const response = await fetch(`/api/sessions/${encodeURIComponent(callSid)}/end`, {
        method: 'POST', signal: AbortSignal.timeout(45000),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Could not confirm cleanup.');
      if (result.saved && result.history_url) {
        const link = /** @type {HTMLAnchorElement} */ (element('history-link'));
        link.href = result.history_url;
        element('history').hidden = false;
      }
      if (result.error) error(result.error);
    }
  } catch (cause) {
    error(cause.message || 'Could not confirm whether the conversation was saved.');
  } finally {
    generation += 1; // Ignore late callbacks from the previous connection.
    client = null;
    await active.disconnect().catch(() => {});
    resetControls();
    stopping = false;
    status('Conversation ended.');
  }
}

startButton.addEventListener('click', async () => {
  const current = ++generation;
  const valid = () => current === generation;
  startButton.disabled = true;
  error('');
  element('history').hidden = true;
  transcript.replaceChildren();
  callSid = null;
  muted = false;
  botReady = uiReady = botSpeaking = stopping = false;
  greeting = true;
  assistantRow = userDraft = null;
  muteButton.textContent = 'Mute';
  muteButton.setAttribute('aria-pressed', 'false');
  status('Allow microphone access to connect…');
  let active;
  try {
    active = new PipecatClient({
      transport: new SmallWebRTCTransport({ iceServers: [] }),
      enableMic: true,
      enableCam: false,
      callbacks: {
        onTrackStarted: (track, participant) => {
          if (!valid() || participant?.local || track.kind !== 'audio') return;
          audio.srcObject = new MediaStream([track]);
          void playbackReady();
        },
        onBotReady: () => {
          if (!valid()) return;
          botReady = true;
          void playbackReady();
        },
        onServerMessage: (data) => {
          if (!valid()) return;
          if (data.type === 'session') {
            callSid = data.call_sid;
            endButton.disabled = false;
            muteButton.disabled = false;
          }
        },
        onLocalAudioLevel: (level) => { if (valid()) meter.value = level; },
        onBotStartedSpeaking: () => {
          if (!valid()) return;
          botSpeaking = true;
          status('Agent speaking…');
        },
        onBotStoppedSpeaking: () => {
          if (!valid() || !botSpeaking) return;
          botSpeaking = false;
          assistantRow = null;
          if (greeting) {
            greeting = false;
            active.enableMic(!muted);
          }
          status(muted ? 'Microphone muted.' : 'Listening…');
        },
        onBotTtsText: (data) => {
          if (!valid() || !data.text) return;
          if (!assistantRow) assistantRow = row('Agent', '');
          assistantRow.textContent += `${assistantRow.textContent ? ' ' : ''}${data.text}`;
          transcript.scrollTop = transcript.scrollHeight;
        },
        onUserTranscript: (data) => {
          if (!valid() || greeting || !data.text) return;
          if (!userDraft) userDraft = row('You', '', true);
          userDraft.textContent = data.text;
          if (data.final) {
            userDraft.parentElement.classList.remove('draft');
            userDraft = null;
            assistantRow = null;
            status('Thinking…');
          }
        },
        onBotLlmStarted: () => { if (valid()) status('Thinking…'); },
        onError: (event) => {
          if (!valid()) return;
          error(event.error || event.message || 'The agent reported a provider error.');
          void stop();
        },
        onDisconnected: () => { if (valid() && !stopping) void stop(); },
      },
    });
  } catch (cause) {
    error(cause.message || 'This browser could not initialize audio.');
    resetControls();
    status('Could not initialize audio.');
    return;
  }
  client = active;
  try {
    await active.initDevices();
    if (!valid()) return;
    active.enableMic(false);
    endButton.disabled = false; // Allows canceling a connection before a session ID arrives.
    status('Connecting…');
    await active.connect({ webrtcRequestParams: { endpoint: '/api/offer' } });
  } catch (cause) {
    if (!valid()) return;
    error(cause.message || 'Connection failed. Check microphone permissions and Settings.');
    await stop();
  }
});

muteButton.addEventListener('click', () => {
  muted = !muted;
  client?.enableMic(!muted && !greeting);
  muteButton.textContent = muted ? 'Unmute' : 'Mute';
  muteButton.setAttribute('aria-pressed', String(muted));
  if (!botSpeaking && !greeting) status(muted ? 'Microphone muted.' : 'Listening…');
});
endButton.addEventListener('click', () => void stop());
element('audio-enable').addEventListener('click', () => void playbackReady());
window.addEventListener('pagehide', () => {
  client?.enableMic(false);
  if (callSid) void fetch(`/api/sessions/${encodeURIComponent(callSid)}/end`, { method: 'POST', keepalive: true }).catch(() => {});
  void client?.disconnect().catch(() => {});
});
