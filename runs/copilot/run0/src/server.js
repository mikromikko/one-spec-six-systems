import { createServer } from './service.js';

const port = Number(process.env.PORT || 3000);
const dataDir = process.env.DATA_DIR || new URL('../data', import.meta.url).pathname;

const server = createServer({ dataDir });

server.listen(port, () => {
  console.log(`Invoice approval service listening on http://localhost:${port}`);
});
