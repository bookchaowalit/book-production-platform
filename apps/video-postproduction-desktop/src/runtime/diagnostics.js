const MAX_DIAGNOSTIC_CHARACTERS = 4096;

const SENSITIVE_ASSIGNMENT = /(^|[\s,;])([A-Za-z_][A-Za-z0-9_]*(?:PASSWORD|TOKEN|SECRET|KEY)[A-Za-z0-9_]*)(\s*[=:]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;]*)/gi;

/** @param {string | null | undefined} text @returns {string} */
function sanitizeDiagnostic(text) {
  const normalized = String(text ?? "")
    .replace(/\r\n?/g, "\n")
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, " ");
  const redacted = normalized.replace(SENSITIVE_ASSIGNMENT, "$1$2$3[REDACTED]");
  return redacted.length > MAX_DIAGNOSTIC_CHARACTERS
    ? redacted.slice(-MAX_DIAGNOSTIC_CHARACTERS)
    : redacted;
}

module.exports = { sanitizeDiagnostic };
