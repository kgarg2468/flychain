#!/usr/bin/env node
/** FlyChain CLI entrypoint. */
import { Command } from 'commander';
import pc from 'picocolors';

import { runInit } from './commands/init.js';
import { runInstrument } from './commands/instrument.js';

export const CLI_VERSION = '0.0.0';

export function buildProgram(): Command {
  const program = new Command();

  program
    .name('flychain')
    .description('FlyChain CLI - capability-improvement flywheel')
    .version(CLI_VERSION);

  program
    .command('init')
    .description('Create flychain.config.json in the current directory')
    .option('--project-id <id>', 'Project identifier')
    .option('--gateway-url <url>', 'FlyChain gateway URL', 'http://localhost:8080')
    .option('--tag <key=value...>', 'Default tag to attach to traces (repeatable)')
    .option('--force', 'Overwrite an existing config', false)
    .action(async (opts) => {
      try {
        await runInit({
          projectId: opts.projectId,
          gatewayUrl: opts.gatewayUrl,
          tag: opts.tag,
          force: Boolean(opts.force),
        });
      } catch (e) {
        console.error(pc.red('error:'), (e as Error).message);
        process.exitCode = 1;
      }
    });

  program
    .command('instrument')
    .description('Wire your repo to the FlyChain gateway')
    .option('--apply', 'Apply changes (default is preview-only)', false)
    .action(async (opts) => {
      try {
        await runInstrument({ apply: Boolean(opts.apply) });
      } catch (e) {
        console.error(pc.red('error:'), (e as Error).message);
        process.exitCode = 1;
      }
    });

  return program;
}

function main(argv: string[]): void {
  const program = buildProgram();
  program.parse(argv);
}

const invokedDirectly =
  typeof process !== 'undefined' &&
  Array.isArray(process.argv) &&
  process.argv[1] !== undefined &&
  (import.meta.url === `file://${process.argv[1]}` ||
    import.meta.url.endsWith(process.argv[1].replace(/\\/g, '/')));

if (invokedDirectly) {
  main(process.argv);
}
