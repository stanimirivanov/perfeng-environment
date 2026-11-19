import assert from 'node:assert/strict';
import { test } from 'node:test';
import { decodeFrame, checkOutcome } from './bff-consumer.mjs';

const issuer = 'https://127.0.0.1:12345/realms/perfeng-fixture';
const expected = { subject: 'fixture-person', runId: 'perf-20260902-120000-abcdef12' };
test('private frames are exact canonical flat JSON, rejecting duplicates, extra data and destination substitution', () => {
  const frame = { phase: 'start', issuer, ca: 'CA', privateKeyPem: 'KEY' };
  assert.deepEqual(decodeFrame(JSON.stringify(frame), 'start'), frame);
  for (const text of [
    '{"phase":"start","phase":"start","issuer":"' + issuer + '","ca":"CA","privateKeyPem":"KEY"}',
    JSON.stringify({ ...frame, issuer: 'https://foreign.example/realms/perfeng-fixture' }),
    JSON.stringify({ ...frame, extra: 'TOKEN-CANARY' }),
    JSON.stringify({ ...frame, ca: {} }),
    ' ' + JSON.stringify(frame),
    JSON.stringify({ phase: 'callback', target: '/console/auth/callback?code=secret' }),
  ]) assert.throws(() => decodeFrame(text, 'start'), /refused/);
  assert.deepEqual(decodeFrame('{"phase":"callback","target":"/console/auth/callback?code=test"}', 'callback'), { phase: 'callback', target: '/console/auth/callback?code=test' });
  assert.throws(() => decodeFrame(JSON.stringify({ phase: 'callback', target: 'https://foreign/callback' }), 'callback'));
});

test('only a minimal verified observation for the independent expected person and Run is a receipt', () => {
  const now = Date.now();
  const result = { ok: true, stage: 'verified-code-observation', issuer, subject: expected.subject, validUntilMs: now + 10000, returnIntent: { kind: 'run', runId: expected.runId }, clearCookie: '__Host-perfeng_login=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0' };
  assert.doesNotThrow(() => checkOutcome(result, issuer, expected, now));
  for (const change of [{ ok: false }, { stage: 'unverified-code' }, { subject: 'foreign' }, { issuer: 'foreign' }, { validUntilMs: now }, { validUntilMs: now + 300001 }, { accessToken: 'TOKEN-CANARY' }, { returnIntent: { kind: 'home' } }, { clearCookie: 'unsafe' }]) {
    assert.throws(() => checkOutcome({ ...result, ...change }, issuer, expected, now), /refused/);
  }
});
