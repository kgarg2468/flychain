import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { VERSION, gatewayBaseUrl, resolveConfig } from './index.js';

describe('sdk-ts', () => {
  it('exposes a version', () => {
    assert.equal(VERSION, '0.0.0');
  });

  it('uses the default gateway URL when env is unset', () => {
    const prev = process.env.FLYCHAIN_GATEWAY_URL;
    delete process.env.FLYCHAIN_GATEWAY_URL;
    try {
      assert.equal(gatewayBaseUrl(), 'http://localhost:8080');
    } finally {
      if (prev !== undefined) process.env.FLYCHAIN_GATEWAY_URL = prev;
    }
  });

  it('honors the env override', () => {
    const prev = process.env.FLYCHAIN_GATEWAY_URL;
    process.env.FLYCHAIN_GATEWAY_URL = 'https://example.test:9999';
    try {
      assert.equal(gatewayBaseUrl(), 'https://example.test:9999');
    } finally {
      if (prev === undefined) delete process.env.FLYCHAIN_GATEWAY_URL;
      else process.env.FLYCHAIN_GATEWAY_URL = prev;
    }
  });

  it('resolves a full config with overrides', () => {
    const cfg = resolveConfig({ gatewayUrl: 'http://x', projectId: 'p1' });
    assert.equal(cfg.gatewayUrl, 'http://x');
    assert.equal(cfg.projectId, 'p1');
  });
});
