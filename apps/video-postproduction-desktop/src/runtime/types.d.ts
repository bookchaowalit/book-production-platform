export type ServiceName = "postgres" | "redis" | "api" | "worker" | "web";

export type ServiceState = "unknown" | "running" | "healthy" | "stopped" | "unhealthy";

export type RuntimePhase =
  | "checking"
  | "starting"
  | "waiting-health"
  | "ready"
  | "needs-action"
  | "stopped"
  | "failed";

export type RuntimeAction = "attached" | "started" | "recover-required" | "stopped" | null;

export type RuntimeCode =
  | "missing-docker"
  | "engine-unavailable"
  | "port-conflict"
  | "partial-runtime"
  | "compose-failed"
  | "health-timeout"
  | "invalid-runtime"
  | null;

export type RuntimeStatus = {
  phase: RuntimePhase;
  action: RuntimeAction;
  code: RuntimeCode;
  message: string;
  services: Record<ServiceName, ServiceState>;
};
