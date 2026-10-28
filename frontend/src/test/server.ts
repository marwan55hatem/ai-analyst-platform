import { setupServer } from "msw/node";

/** Shared MSW server; each test registers its own handlers with server.use(). */
export const server = setupServer();
