const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { createLauncherBridge, IPC_CHANNELS } = require("../src/security/launcher-bridge");

/** @typedef {import("../src/runtime/types").RuntimeStatus} RuntimeStatus */
/** @typedef {(event: unknown, status: RuntimeStatus) => void} StatusListener */

/** @type {RuntimeStatus} */
const status = {
  phase: "ready",
  action: "attached",
  code: null,
  message: "พร้อมใช้งาน",
  services: { postgres: "healthy", redis: "healthy", api: "healthy", worker: "healthy", web: "healthy" },
};

function createFakeIpcRenderer() {
  /** @type {Array<{ channel: string, listener: StatusListener }>} */
  const registered = [];
  /** @type {Array<{ channel: string, listener: StatusListener }>} */
  const removed = [];
  /** @type {string[]} */
  const invoked = [];
  return {
    registered,
    removed,
    invoked,
    /** @param {string} channel */
    invoke: async (channel) => {
      invoked.push(channel);
      return status;
    },
    /** @param {string} channel @param {StatusListener} listener */
    on: (channel, listener) => {
      registered.push({ channel, listener });
    },
    /** @param {string} channel @param {StatusListener} listener */
    removeListener: (channel, listener) => {
      removed.push({ channel, listener });
    },
    /** @param {string} channel @param {RuntimeStatus} payload */
    emit: (channel, payload) => {
      for (const entry of registered) {
        if (entry.channel === channel && !removed.some((item) => item.listener === entry.listener)) {
          entry.listener({}, payload);
        }
      }
    },
  };
}

test("launcher bridge exposes exactly four methods on fixed IPC channels", async () => {
  const fake = createFakeIpcRenderer();
  const bridge = createLauncherBridge(fake);

  assert.deepEqual(Object.keys(bridge).sort(), ["getStatus", "onStatus", "recover", "start"]);
  assert.equal(IPC_CHANNELS.getStatus, "video-studio:get-status");
  assert.equal(IPC_CHANNELS.start, "video-studio:start");
  assert.equal(IPC_CHANNELS.recover, "video-studio:recover");
  assert.equal(IPC_CHANNELS.status, "video-studio:status");
  assert.equal(await bridge.getStatus(), status);
  assert.equal(await bridge.start(), status);
  assert.equal(await bridge.recover(), status);
  assert.deepEqual(fake.invoked, [IPC_CHANNELS.getStatus, IPC_CHANNELS.start, IPC_CHANNELS.recover]);
});

test("status unsubscribe removes only its paired listener", () => {
  const fake = createFakeIpcRenderer();
  const bridge = createLauncherBridge(fake);
  /** @type {RuntimeStatus[]} */
  const firstReceived = [];
  /** @type {RuntimeStatus[]} */
  const secondReceived = [];
  const unsubscribeFirst = bridge.onStatus((nextStatus) => firstReceived.push(nextStatus));
  bridge.onStatus((nextStatus) => secondReceived.push(nextStatus));

  unsubscribeFirst();
  unsubscribeFirst();
  fake.emit(IPC_CHANNELS.status, status);

  assert.equal(fake.removed.length, 1);
  assert.equal(fake.removed[0].listener, fake.registered[0].listener);
  assert.deepEqual(firstReceived, []);
  assert.deepEqual(secondReceived, [status]);
});

test("sandboxed preload exposes working videoStudio IPC methods without local module loading", async () => {
  const preloadPath = path.join(__dirname, "../src/preload.js");
  const source = readFileSync(preloadPath, "utf8");
  const fake = createFakeIpcRenderer();
  /** @type {Array<{ name: string, value: object }>} */
  const exposed = [];
  const contextBridge = {
    /** @param {string} name @param {object} value */
    exposeInMainWorld: (name, value) => exposed.push({ name, value }),
  };
  /** @param {string} specifier */
  const requireStub = (specifier) => {
    if (specifier === "electron") return { contextBridge, ipcRenderer: fake };
    throw new Error(`unexpected preload dependency: ${specifier}`);
  };

  vm.runInNewContext(source, { require: requireStub });

  assert.equal(exposed.length, 1);
  assert.equal(exposed[0].name, "videoStudio");
  assert.deepEqual(Object.keys(exposed[0].value).sort(), ["getStatus", "onStatus", "recover", "start"]);
  assert.equal("ipcRenderer" in exposed[0].value, false);
  assert.equal("shell" in exposed[0].value, false);
  assert.equal("docker" in exposed[0].value, false);

  const bridge = /** @type {{
   * getStatus: () => Promise<RuntimeStatus>,
   * start: () => Promise<RuntimeStatus>,
   * recover: () => Promise<RuntimeStatus>,
   * onStatus: (listener: (nextStatus: RuntimeStatus) => void) => () => void
   * }} */ (exposed[0].value);
  assert.equal(await bridge.getStatus(), status);
  assert.equal(await bridge.start(), status);
  assert.equal(await bridge.recover(), status);
  assert.deepEqual(fake.invoked, [IPC_CHANNELS.getStatus, IPC_CHANNELS.start, IPC_CHANNELS.recover]);

  /** @type {RuntimeStatus[]} */
  const received = [];
  const unsubscribe = bridge.onStatus((nextStatus) => received.push(nextStatus));
  fake.emit(IPC_CHANNELS.status, status);
  unsubscribe();
  fake.emit(IPC_CHANNELS.status, status);
  assert.deepEqual(received, [status]);
});
