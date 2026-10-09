import type { RuntimeStatus } from "./runtime/types";

declare global {
  interface Window {
    readonly videoStudio: {
      getStatus: () => Promise<RuntimeStatus>;
      start: () => Promise<RuntimeStatus>;
      recover: () => Promise<RuntimeStatus>;
      onStatus: (listener: (status: RuntimeStatus) => void) => () => void;
    };
  }
}

export {};
