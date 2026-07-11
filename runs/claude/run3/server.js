import { initDb } from './src/db.js';
import { createApp } from './src/app.js';
import { seed } from './src/seed.js';

const db = initDb();

// Seed demo users on first run so the service is usable immediately.
const created = seed(db);
if (created) console.log(`Seeded ${created} demo user(s).`);

const app = createApp(db);
const port = process.env.PORT || 3000;

app.listen(port, () => {
  console.log(`Invoice approval service listening on http://localhost:${port}`);
  console.log('Demo accounts (password: password123): submitter1@nordvik.example, approver1@nordvik.example, ...');
});
