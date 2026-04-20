import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { runInit } from '../commands/init.js';
import { parseTags } from '../commands/init.js';
import { configPath } from '../lib/config.js';

function withTempDir<T>(fn: (dir: string) => Promise<T> | T): Promise<T> {
  const dir = mkdtempSync(join(tmpdir(), 'flychain-init-'));
  const cleanup = () => rmSync(dir, { recursive: true, force: true });
  return Promise.resolve(fn(dir)).finally(cleanup);
}

describe('parseTags', () => {
  it('parses key=value pairs', () => {
    assert.deepEqual(parseTags(['env=dev', 'team=core']), { env: 'dev', team: 'core' });
  });
  it('ignores malformed entries', () => {
    assert.deepEqual(parseTags(['bad', '=oops', 'k=v']), { k: 'v' });
  });
  it('handles undefined', () => {
    assert.deepEqual(parseTags(undefined), {});
  });
});

describe('runInit', () => {
  it('writes flychain.config.json with defaults', async () => {
    await withTempDir(async (dir) => {
      const { path, created } = await runInit({ cwd: dir, projectId: 'test-p' });
      assert.equal(created, true);
      assert.equal(path, configPath(dir));
      const json = JSON.parse(readFileSync(path, 'utf8'));
      assert.equal(json.projectId, 'test-p');
      assert.equal(json.gatewayUrl, 'http://localhost:8080');
      assert.equal(json.version, 1);
    });
  });

  it('refuses to overwrite without --force', async () => {
    await withTempDir(async (dir) => {
      await runInit({ cwd: dir, projectId: 'p' });
      await assert.rejects(runInit({ cwd: dir, projectId: 'p' }), /already exists/);
    });
  });

  it('overwrites when force=true', async () => {
    await withTempDir(async (dir) => {
      await runInit({ cwd: dir, projectId: 'p1' });
      const res = await runInit({ cwd: dir, projectId: 'p2', force: true });
      assert.equal(res.created, false);
      const json = JSON.parse(readFileSync(res.path, 'utf8'));
      assert.equal(json.projectId, 'p2');
    });
  });

  it('records tags in config', async () => {
    await withTempDir(async (dir) => {
      const res = await runInit({
        cwd: dir,
        projectId: 'p',
        tag: ['env=dev', 'service=api'],
      });
      const json = JSON.parse(readFileSync(res.path, 'utf8'));
      assert.deepEqual(json.tags, { env: 'dev', service: 'api' });
    });
  });
});
