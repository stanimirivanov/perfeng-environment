/** Private integration consumer of the pinned Console public factory; no browser/server route. */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const profile = 'perfeng-bff-keycloak-transaction-v1';
const checks = ['BFF-state-cookie-admission', 'BFF-PKCE-code-exchange', 'signed-nonce-subject', 'replay-refused', 'run-return-intent'];
const exact = (value, names) => value && typeof value === 'object' && !Array.isArray(value) && Object.keys(value).sort().join(',') === names;
const refuse = () => { throw new Error('refused'); };

/** Accept only this producer's canonical flat JSON; reserialization also refuses duplicate names. */
export function decodeFrame(text, phase) {
  const value = JSON.parse(text);
  if (!exact(value, phase === 'start' ? 'ca,issuer,phase,privateKeyPem' : 'phase,target') ||
      !['start', 'callback'].includes(phase) || value.phase !== phase || JSON.stringify(value) !== text ||
      !Object.values(value).every(item => typeof item === 'string' && item.length <= 16384 && /^[\x09\x0a\x0d\x20-\x7e]*$/.test(item))) refuse();
  if (phase === 'start') {
    const issuer = new URL(value.issuer);
    if (issuer.href !== value.issuer || issuer.protocol !== 'https:' || issuer.hostname !== '127.0.0.1' || !issuer.port || issuer.pathname !== '/realms/perfeng-fixture' || issuer.username || issuer.password || issuer.search || issuer.hash) refuse();
  } else if (value.target.length > 8192 || !value.target.startsWith('/console/auth/callback?') || !/^[\x21-\x7e]+$/.test(value.target)) refuse();
  return value;
}

/** Validate the component result against expectations fixed independently of returned JWT claims. */
export function checkOutcome(value, issuer, expected, now) {
  if (!exact(value, 'clearCookie,issuer,ok,returnIntent,stage,subject,validUntilMs') || value.ok !== true ||
      value.stage !== 'verified-code-observation' || value.issuer !== issuer || value.subject !== expected.subject ||
      !Number.isSafeInteger(value.validUntilMs) || value.validUntilMs <= now || value.validUntilMs - now > 300000 ||
      !exact(value.returnIntent, 'kind,runId') || value.returnIntent.kind !== 'run' || value.returnIntent.runId !== expected.runId ||
      value.clearCookie !== '__Host-perfeng_login=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0') refuse();
}

/** Bounded fatal-UTF8 two-frame reader; EOF is required before dispatching the callback. */
function reader() {
  const iterator = process.stdin[Symbol.asyncIterator]();
  const decoder = new TextDecoder('utf-8', { fatal: true });
  let text = '';
  let bytes = 0;
  async function line() {
    while (!text.includes('\n')) {
      const chunk = await iterator.next();
      if (chunk.done) refuse();
      bytes += chunk.value.length;
      if (bytes > 65536) refuse();
      text += decoder.decode(chunk.value, { stream: true });
    }
    const end = text.indexOf('\n');
    const result = text.slice(0, end);
    text = text.slice(end + 1);
    return result;
  }
  return {
    line,
    async end() {
      if (text || !(await iterator.next()).done || decoder.decode()) refuse();
    },
  };
}

/** Execute only the exported immutable SDK selected by the parent; emit no credential diagnostics. */
async function main() {
  const controller = new AbortController();
  const timer = setTimeout(() => { controller.abort(); process.stdin.destroy(); }, 30000);
  try {
    if (process.argv.length !== 3 || process.execArgv.length) refuse();
    const expected = JSON.parse(readFileSync(new URL('./console.lock.json', import.meta.url), 'utf8'));
    if (process.versions.node !== expected.node) refuse();
    const require = createRequire(pathToFileURL(resolve(process.argv[2], 'package.json')));
    const { Effect } = await import(pathToFileURL(require.resolve('effect')).href);
    const { makeVerifiedCodeExchange } = await import(pathToFileURL(require.resolve('#perfeng/bff-verified-exchange')).href);
    const input = reader();
    const start = decodeFrame(await input.line(), 'start');
    await Effect.runPromise(Effect.scoped(Effect.gen(function* () {
      const owner = yield* makeVerifiedCodeExchange({ issuer: start.issuer, ca: start.ca, privateKeyPem: start.privateKeyPem, consoleOrigin: 'https://127.0.0.1' });
      const intent = { kind: 'run', runId: expected.runId };
      const begun = owner.begin(intent);
      if (!begun) refuse();
      // Cookie binding and verifier stay in this process. Only the public authorization URL leaves it.
      const cookies = [begun.setCookie.split(';')[0]];
      process.stdout.write(JSON.stringify({ phase: 'authorize', authorizationUrl: begun.authorizationUrl }) + '\n');
      const callback = decodeFrame(yield* Effect.promise(() => input.line()), 'callback');
      yield* Effect.promise(() => input.end());
      const unbound = yield* owner.complete(callback.target, []);
      if (unbound.ok || unbound.reason !== 'invalid_callback') refuse();
      const operation = owner.complete(callback.target, cookies);
      const result = yield* operation;
      checkOutcome(result, start.issuer, expected, Date.now());
      const replay = yield* operation;
      if (replay.ok || replay.reason !== 'invalid_callback') refuse();
      owner.stop();
      if (owner.begin(intent) !== undefined) refuse();
    })), { signal: controller.signal });
    process.stdout.write(JSON.stringify({ profile, status: 'PASS', admission: 'NOT_ADMITTED', checks }) + '\n');
  } catch {
    process.stdout.write(JSON.stringify({ profile, status: 'FAIL', admission: 'NOT_ADMITTED' }) + '\n');
    process.exitCode = 1;
  } finally {
    clearTimeout(timer);
    process.stdin.destroy();
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) await main();
