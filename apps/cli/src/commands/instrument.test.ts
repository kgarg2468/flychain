import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

import { runInit } from '../commands/init.js';
import { runInstrument } from '../commands/instrument.js';

function withTempProject<T>(
  files: Record<string, string>,
  fn: (dir: string) => Promise<T> | T,
): Promise<T> {
  const dir = mkdtempSync(join(tmpdir(), 'flychain-inst-'));
  for (const [rel, content] of Object.entries(files)) {
    const abs = join(dir, rel);
    mkdirSync(abs.replace(/\/[^/]+$/, ''), { recursive: true });
    writeFileSync(abs, content, 'utf8');
  }
  const cleanup = () => rmSync(dir, { recursive: true, force: true });
  return Promise.resolve(fn(dir)).finally(cleanup);
}

describe('runInstrument', () => {
  it('errors when flychain.config.json is missing', async () => {
    await withTempProject({ 'app.py': `client = OpenAI()` }, async (dir) => {
      await assert.rejects(runInstrument({ cwd: dir }), /flychain init/);
    });
  });

  it('previews changes without writing when --apply is false', async () => {
    await withTempProject(
      {
        'src/app.py': `from openai import OpenAI\nclient = OpenAI(api_key="sk")\n`,
        'src/client.ts': `import OpenAI from 'openai';\nconst c = new OpenAI({ apiKey: 'sk' });\n`,
      },
      async (dir) => {
        await runInit({ cwd: dir, projectId: 'p' });
        const result = await runInstrument({ cwd: dir, apply: false });
        assert.equal(result.detections, 2);
        assert.equal(result.filesChanged, 2);
        // Files should be untouched
        assert.match(readFileSync(join(dir, 'src/app.py'), 'utf8'), /OpenAI\(api_key="sk"\)/);
        assert.match(
          readFileSync(join(dir, 'src/client.ts'), 'utf8'),
          /new OpenAI\(\{ apiKey: 'sk' \}\)/,
        );
      },
    );
  });

  it('rewrites files when --apply is true', async () => {
    await withTempProject(
      {
        'src/app.py': `from openai import OpenAI\nclient = OpenAI(api_key="sk")\n`,
      },
      async (dir) => {
        await runInit({ cwd: dir, projectId: 'my-proj' });
        const result = await runInstrument({ cwd: dir, apply: true });
        assert.equal(result.filesChanged, 1);
        const rewritten = readFileSync(join(dir, 'src/app.py'), 'utf8');
        assert.match(rewritten, /base_url="http:\/\/localhost:8080"/);
        assert.match(rewritten, /"x-flychain-project": "my-proj"/);
      },
    );
  });

  it('skips node_modules and .venv by default', async () => {
    await withTempProject(
      {
        'node_modules/openai/index.ts': `new OpenAI({})`,
        '.venv/lib/pkg.py': `OpenAI(api_key="x")`,
        'src/main.py': `client = OpenAI()`,
      },
      async (dir) => {
        await runInit({ cwd: dir, projectId: 'p' });
        const res = await runInstrument({ cwd: dir, apply: true });
        assert.equal(res.detections, 1, 'only src/main.py should be detected');
        const rewritten = readFileSync(join(dir, 'src/main.py'), 'utf8');
        assert.match(rewritten, /base_url="http:/);
      },
    );
  });

  it('is idempotent on already-instrumented files', async () => {
    await withTempProject(
      {
        'src/app.py': `client = OpenAI(api_key="sk")\n`,
      },
      async (dir) => {
        await runInit({ cwd: dir, projectId: 'p' });
        await runInstrument({ cwd: dir, apply: true });
        const afterFirst = readFileSync(join(dir, 'src/app.py'), 'utf8');
        await runInstrument({ cwd: dir, apply: true });
        const afterSecond = readFileSync(join(dir, 'src/app.py'), 'utf8');
        assert.equal(afterFirst, afterSecond);
      },
    );
  });
});
