import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { detect, languageOf, rewrite } from '../lib/detect.js';
import { buildConfig } from '../lib/config.js';

const cfg = buildConfig('proj-1', { tags: { env: 'dev' } });

describe('languageOf', () => {
  it('detects Python from .py', () => {
    assert.equal(languageOf('/tmp/foo.py'), 'python');
  });
  it('detects TypeScript from various JS/TS extensions', () => {
    for (const ext of ['.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs']) {
      assert.equal(languageOf(`/tmp/x${ext}`), 'typescript', ext);
    }
  });
  it('returns null for unknown', () => {
    assert.equal(languageOf('/tmp/x.txt'), null);
  });
});

describe('detect: typescript', () => {
  it('finds new OpenAI() and new Anthropic()', () => {
    const src = `
      import OpenAI from 'openai';
      import Anthropic from '@anthropic-ai/sdk';
      const a = new OpenAI({ apiKey: 'sk' });
      const b = new Anthropic({ apiKey: 'sk' });
    `;
    const found = detect(src, 'typescript');
    assert.equal(found.length, 2);
    assert.equal(found[0]!.kind, 'openai');
    assert.equal(found[1]!.kind, 'anthropic');
  });

  it('finds bare OpenAI() call', () => {
    const src = `const c = OpenAI({ apiKey: "x" });`;
    const found = detect(src, 'typescript');
    assert.equal(found.length, 1);
    assert.equal(found[0]!.kind, 'openai');
  });

  it('marks already-instrumented calls', () => {
    const src = `new OpenAI({ baseURL: 'http://localhost:8080', apiKey: 'x' })`;
    const [d] = detect(src, 'typescript');
    assert.ok(d);
    assert.equal(d.alreadyInstrumented, true);
  });
});

describe('detect: python', () => {
  it('finds OpenAI() and Anthropic()', () => {
    const src = `
import openai
import anthropic
client_a = OpenAI(api_key="sk")
client_b = openai.OpenAI(api_key="sk")
client_c = Anthropic(api_key="sk")
client_d = anthropic.Anthropic(api_key="sk")
`;
    const found = detect(src, 'python');
    assert.equal(found.length, 4);
    const kinds = found.map((f) => f.kind).sort();
    assert.deepEqual(kinds, ['anthropic', 'anthropic', 'openai', 'openai']);
  });
});

describe('rewrite: typescript', () => {
  it('injects baseURL and defaultHeaders into an OpenAI constructor', () => {
    const src = `const c = new OpenAI({ apiKey: 'sk-test' });`;
    const dets = detect(src, 'typescript');
    const { source, changed, applied } = rewrite(src, dets, cfg);
    assert.equal(changed, true);
    assert.equal(applied.length, 1);
    assert.match(source, /baseURL:\s*'http:\/\/localhost:8080'/);
    assert.match(source, /x-flychain-project/);
    assert.match(source, /apiKey: 'sk-test'/);
  });

  it('skips already-instrumented constructors', () => {
    const src = `new OpenAI({ baseURL: 'http://localhost:8080', apiKey: 'x' })`;
    const dets = detect(src, 'typescript');
    const { changed } = rewrite(src, dets, cfg);
    assert.equal(changed, false);
  });

  it('wraps a variable-ref argument with overrides', () => {
    const src = `const c = new OpenAI(options);`;
    const dets = detect(src, 'typescript');
    const { source } = rewrite(src, dets, cfg);
    assert.match(source, /\{\s*\.\.\.options/);
    assert.match(source, /baseURL:/);
  });
});

describe('rewrite: python', () => {
  it('adds base_url and default_headers kwargs', () => {
    const src = `client = OpenAI(api_key="sk-test")`;
    const dets = detect(src, 'python');
    const { source, changed, applied } = rewrite(src, dets, cfg);
    assert.equal(changed, true);
    assert.equal(applied.length, 1);
    assert.match(source, /base_url="http:\/\/localhost:8080"/);
    assert.match(source, /default_headers=\{/);
    assert.match(source, /"x-flychain-project": "proj-1"/);
  });

  it('handles empty constructor', () => {
    const src = `client = OpenAI()`;
    const dets = detect(src, 'python');
    const { source, changed } = rewrite(src, dets, cfg);
    assert.equal(changed, true);
    assert.match(source, /OpenAI\(base_url=/);
  });
});
