const assert = require("node:assert/strict");
const test = require("node:test");

const {
  getApprovedExternalUrl,
  isAllowedNavigation,
  isTrustedLauncherSender
} = require("../src/security/window-policy");
const {
  createLauncherWindowOptions,
  createStudioWindowOptions
} = require("../src/security/window-options");

test("test_external_navigation_is_blocked", () => {
  assert.equal(isAllowedNavigation("solo-video://launcher/index.html", "launcher"), true);
  assert.equal(isAllowedNavigation("solo-video://launcher/secret.txt", "launcher"), false);
  assert.equal(isAllowedNavigation("solo-video://launcher/index.html?next=secret", "launcher"), false);
  assert.equal(isAllowedNavigation("http://127.0.0.1:3400/projects", "studio"), true);
  assert.equal(isAllowedNavigation("http://localhost:3400/projects", "studio"), false);
  assert.equal(isAllowedNavigation("https://example.com", "studio"), false);
  assert.equal(isAllowedNavigation("http://127.0.0.1:3401", "studio"), false);
  assert.equal(isAllowedNavigation("http://user@127.0.0.1:3400/projects", "studio"), false);
  assert.equal(isAllowedNavigation("solo-video://launcher/index.html", "studio"), false);
  assert.equal(isAllowedNavigation("http://127.0.0.1:3400/projects", "launcher"), false);
  assert.equal(isAllowedNavigation("solo-video://other/index.html", "launcher"), false);
  assert.equal(isAllowedNavigation("solo-video://launcher:444/index.html", "launcher"), false);
  assert.equal(isAllowedNavigation("not a URL", "launcher"), false);
});

test("launcher IPC trusts only the actual main frame object", () => {
  const mainFrame = {
    origin: "solo-video://launcher",
    url: "solo-video://launcher/index.html",
  };
  const lookalikeFrame = { ...mainFrame };

  assert.equal(isTrustedLauncherSender(mainFrame, mainFrame, 7, 7), true);
  assert.equal(isTrustedLauncherSender(lookalikeFrame, mainFrame, 7, 7), false);
});

test("test_untrusted_sender_is_rejected", () => {
  const launcherDocument = {
    origin: "solo-video://launcher",
    url: "solo-video://launcher/index.html",
  };
  const invalidPath = { ...launcherDocument, url: "solo-video://launcher/secret.txt" };
  const foreignOrigin = { ...launcherDocument, origin: "http://127.0.0.1:3400" };
  const foreignHost = { ...launcherDocument, origin: "solo-video://other" };
  assert.equal(isTrustedLauncherSender(invalidPath, invalidPath), false);
  assert.equal(isTrustedLauncherSender(foreignOrigin, foreignOrigin), false);
  assert.equal(isTrustedLauncherSender(foreignHost, foreignHost), false);
  assert.equal(isTrustedLauncherSender(launcherDocument, launcherDocument, 7, 8), false);
  assert.equal(isTrustedLauncherSender(launcherDocument, launcherDocument, 7, 7), true);
  assert.equal(isTrustedLauncherSender(launcherDocument, launcherDocument, 7), false);
  assert.equal(isTrustedLauncherSender(null, launcherDocument), false);
  assert.equal(isTrustedLauncherSender(launcherDocument, null), false);
});

test("external browser approval is limited to the exact Docker Desktop install guide", () => {
  const approved = "https://docs.docker.com/desktop/setup/install/windows-install/";

  assert.equal(getApprovedExternalUrl(approved), approved);
  assert.equal(getApprovedExternalUrl("https://example.com/"), null);
  assert.equal(getApprovedExternalUrl(`${approved}?redirect=https://example.com`), null);
  assert.equal(getApprovedExternalUrl("https://docs.docker.com/desktop/setup/install/windows-install"), null);
  assert.equal(getApprovedExternalUrl(null), null);
});

test("launcher preferences keep the privileged preload behind secure renderer settings", () => {
  const options = createLauncherWindowOptions("C:/app/preload.js");
  const preferences = options.webPreferences;

  assert.equal(preferences.nodeIntegration, false);
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.webSecurity, true);
  assert.equal(preferences.preload, "C:/app/preload.js");
});

test("studio preferences retain security flags and contain no preload property", () => {
  const options = createStudioWindowOptions();
  const preferences = options.webPreferences;

  assert.equal(preferences.nodeIntegration, false);
  assert.equal(preferences.contextIsolation, true);
  assert.equal(preferences.sandbox, true);
  assert.equal(preferences.webSecurity, true);
  assert.equal("preload" in preferences, false);
});

test("launcher preferences require a supplied preload path", () => {
  assert.throws(() => createLauncherWindowOptions(""), TypeError);
});
