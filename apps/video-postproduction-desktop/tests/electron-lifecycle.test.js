const assert = require("node:assert/strict");
const test = require("node:test");
const { acquireSingleInstanceLock, isSquirrelStartup, confirmStopEngine } = require("../src/electron/lifecycle");

/** @typedef {(...args: unknown[]) => void} EventListener */
/** @typedef {{ type: "warning", title: string, message: string, detail: string, buttons: string[], defaultId: number, cancelId: number, noLink: true }} CapturedDialogOptions */
/** @typedef {{ isDestroyed?: () => boolean, isMinimized?: () => boolean, restore?: () => void, show?: () => void, focus?: () => void }} ParentWindow */

function createEventTarget() {
  /** @type {Map<string, EventListener[]>} */
  const listeners = new Map();
  return {
    /** @param {string} event @param {EventListener} listener */
    on: (event, listener) => {
      const existing = listeners.get(event) ?? [];
      existing.push(listener);
      listeners.set(event, existing);
    },
    /** @param {string} event @param {...unknown} args */
    emit: (event, ...args) => {
      for (const listener of listeners.get(event) ?? []) listener(...args);
    },
  };
}

/** @param {{ lock?: boolean, response?: number }} [options] */
function createHarness(options = {}) {
  /** @type {string[]} */
  const events = [];
  const appEvents = createEventTarget();
  const windowEvents = createEventTarget();
  let lockCalls = 0;
  let stopCalls = 0;
  /** @type {{ minimized: boolean, destroyed: boolean, restore: () => void, show: () => void, focus: () => void, isMinimized: () => boolean, isDestroyed: () => boolean, emit: (event: string, ...args: unknown[]) => void }} */
  const launcherWindow = {
    minimized: true,
    destroyed: false,
    restore: () => events.push("restore"),
    show: () => events.push("show"),
    focus: () => events.push("focus"),
    isMinimized: () => launcherWindow.minimized,
    isDestroyed: () => launcherWindow.destroyed,
    emit: windowEvents.emit,
  };
  const app = {
    on: appEvents.on,
    emit: appEvents.emit,
    requestSingleInstanceLock: () => {
      lockCalls += 1;
      events.push("lock");
      return options.lock ?? true;
    },
    quit: () => events.push("quit"),
  };
  const manager = {
    stop: async () => {
      stopCalls += 1;
      events.push("manager-stop");
      return { phase: "stopped" };
    },
  };
  /** @type {Array<{ options: CapturedDialogOptions, parentWindow: ParentWindow | undefined }>} */
  const dialogs = [];
  /** @param {CapturedDialogOptions} dialogOptions @param {ParentWindow | undefined} parentWindow */
  const showMessageBox = async (dialogOptions, parentWindow) => {
    dialogs.push({ options: dialogOptions, parentWindow });
    events.push("confirm-stop");
    return { response: options.response ?? 1 };
  };

  return {
    app,
    dialogs,
    events,
    get launcherWindow() { return launcherWindow; },
    get lockCalls() { return lockCalls; },
    get stopCalls() { return stopCalls; },
    get manager() { return manager; },
    get showMessageBox() { return showMessageBox; },
  };
}

test("does not continue when the single-instance lock is unavailable", () => {
  const fake = createHarness({ lock: false });

  assert.equal(acquireSingleInstanceLock({
    app: fake.app,
    getLauncherWindow: () => fake.launcherWindow,
  }), false);
  assert.equal(fake.lockCalls, 1);
  assert.deepEqual(fake.events, ["lock", "quit"]);
});

test("second-instance restores and focuses the existing launcher", () => {
  const fake = createHarness();

  assert.equal(acquireSingleInstanceLock({
    app: fake.app,
    getLauncherWindow: () => fake.launcherWindow,
  }), true);
  fake.events.length = 0;
  fake.app.emit("second-instance");

  assert.deepEqual(fake.events, ["restore", "show", "focus"]);
});

test("recognizes Squirrel install, update, uninstall, and obsolete startup arguments", () => {
  for (const argument of ["--squirrel-install", "--squirrel-updated", "--squirrel-uninstall", "--squirrel-obsolete"]) {
    assert.equal(isSquirrelStartup(["app.exe", argument]), true, argument);
  }
  assert.equal(isSquirrelStartup(["app.exe", "--normal-launch"]), false);
  assert.equal(isSquirrelStartup(["app.exe", "--normal-launch", "--squirrel-install"]), false);
  assert.equal(isSquirrelStartup([]), false);
});

test("window close and app quit paths never stop the runtime manager", () => {
  const fake = createHarness();
  acquireSingleInstanceLock({ app: fake.app, getLauncherWindow: () => fake.launcherWindow });

  fake.launcherWindow.emit("close");
  fake.launcherWindow.emit("closed");
  fake.app.emit("before-quit");
  fake.app.emit("will-quit");
  fake.app.emit("quit");

  assert.equal(fake.stopCalls, 0);
});

test("declining Stop Engine never stops the runtime manager", async () => {
  const fake = createHarness({ response: 1 });

  assert.equal(await confirmStopEngine({
    showMessageBox: fake.showMessageBox,
    manager: fake.manager,
    parentWindow: fake.launcherWindow,
  }), false);

  assert.equal(fake.dialogs.length, 1);
  assert.equal(fake.stopCalls, 0);
  assert.equal(fake.dialogs[0].parentWindow, fake.launcherWindow);
});

test("explicit Stop Engine confirmation calls manager.stop exactly once", async () => {
  const fake = createHarness({ response: 0 });

  const result = await confirmStopEngine({
    showMessageBox: fake.showMessageBox,
    manager: fake.manager,
    parentWindow: fake.launcherWindow,
  });

  assert.deepEqual(result, { phase: "stopped" });
  assert.equal(fake.dialogs.length, 1);
  assert.equal(fake.stopCalls, 1);
  assert.equal(fake.dialogs[0].options.type, "warning");
  assert.deepEqual(fake.dialogs[0].options.buttons, ["หยุด Engine", "ยกเลิก"]);
});
