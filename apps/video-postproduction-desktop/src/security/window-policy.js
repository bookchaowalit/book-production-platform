const DOCKER_DESKTOP_INSTALL_URL =
  "https://docs.docker.com/desktop/setup/install/windows-install/";
const LAUNCHER_DOCUMENT_URL = "solo-video://launcher/index.html";

/**
 * @param {unknown} url
 * @param {unknown} windowKind
 * @returns {boolean}
 */
function isAllowedNavigation(url, windowKind) {
  if (typeof url !== "string") {
    return false;
  }

  try {
    const parsed = new URL(url);
    const authority = /^([a-z][a-z\d+.-]*):\/\/([^/?#]*)/i.exec(url)?.[2];

    if (windowKind === "launcher") {
      return url === LAUNCHER_DOCUMENT_URL;
    }

    if (windowKind === "studio") {
      return (
        parsed.protocol === "http:" &&
        parsed.hostname === "127.0.0.1" &&
        parsed.port === "3400" &&
        parsed.username === "" &&
        parsed.password === "" &&
        authority === "127.0.0.1:3400"
      );
    }

    return false;
  } catch {
    return false;
  }
}

/**
 * @param {unknown} url
 * @returns {string | null}
 */
function getApprovedExternalUrl(url) {
  return url === DOCKER_DESKTOP_INSTALL_URL ? DOCKER_DESKTOP_INSTALL_URL : null;
}

/**
 * @param {{origin?: string, url?: string} | null | undefined} senderFrame
 * @param {object | null | undefined} mainFrame
 * @param {number | undefined} [senderId]
 * @param {number | undefined} [launcherId]
 * @returns {boolean}
 */
function isTrustedLauncherSender(senderFrame, mainFrame, senderId, launcherId) {
  if (
    !senderFrame ||
    senderFrame !== mainFrame ||
    senderFrame?.origin !== "solo-video://launcher" ||
    senderFrame.url !== LAUNCHER_DOCUMENT_URL
  ) {
    return false;
  }

  if (senderId !== undefined || launcherId !== undefined) {
    return senderId !== undefined && launcherId !== undefined && senderId === launcherId;
  }

  return true;
}

module.exports = {
  DOCKER_DESKTOP_INSTALL_URL,
  getApprovedExternalUrl,
  isAllowedNavigation,
  isTrustedLauncherSender
};
