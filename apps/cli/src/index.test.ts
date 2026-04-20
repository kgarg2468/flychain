import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { buildProgram, CLI_VERSION } from './index.js';

describe('cli', () => {
  it('reports its version', () => {
    assert.equal(CLI_VERSION, '0.0.0');
  });

  it('registers init and instrument commands', () => {
    const program = buildProgram();
    const names = program.commands.map((c) => c.name()).sort();
    assert.deepEqual(names, ['init', 'instrument']);
  });

  it('instrument has --apply flag', () => {
    const program = buildProgram();
    const instrument = program.commands.find((c) => c.name() === 'instrument');
    assert.ok(instrument, 'instrument command should exist');
    const applyOpt = instrument.options.find((o) => o.long === '--apply');
    assert.ok(applyOpt, '--apply flag should exist');
  });

  it('init has --project-id and --gateway-url flags', () => {
    const program = buildProgram();
    const init = program.commands.find((c) => c.name() === 'init');
    assert.ok(init);
    const longs = init.options.map((o) => o.long).sort();
    assert.ok(longs.includes('--project-id'));
    assert.ok(longs.includes('--gateway-url'));
    assert.ok(longs.includes('--force'));
  });
});
