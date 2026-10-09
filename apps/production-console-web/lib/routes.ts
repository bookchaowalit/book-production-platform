const ID = 'sha256:[0-9a-f]{64}';
const PROJECT = '[a-z0-9]+(?:-[a-z0-9]+)*';

const GET_ROUTES = [
  /^v1\/health$/,
  /^v1\/registry$/,
  /^v1\/artifacts$/,
  new RegExp(`^v1/artifacts/${ID}(?:/lineage|/content)?$`),
  /^v1\/projects$/,
  new RegExp(`^v1/projects/${PROJECT}$`),
];

const POST_ROUTES = [
  /^v1\/send$/,
  /^v1\/projects$/,
  new RegExp(`^v1/projects/${PROJECT}/(?:review|assign|package)$`),
];

export const allowedGet = (route: string): boolean => GET_ROUTES.some((pattern) => pattern.test(route));
export const allowedPost = (route: string): boolean => POST_ROUTES.some((pattern) => pattern.test(route));
