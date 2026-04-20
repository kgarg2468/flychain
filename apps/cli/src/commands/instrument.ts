/** `flychain instrument` command. */
import { readFileSync, writeFileSync } from 'node:fs';
import { relative } from 'node:path';
import pc from 'picocolors';

import { loadConfig, type FlyChainProjectConfig } from '../lib/config.js';
import { detect, languageOf, rewrite, type Detection } from '../lib/detect.js';
import { discoverSourceFiles } from '../lib/discover.js';

export interface InstrumentOptions {
  cwd?: string;
  apply?: boolean;
  includePath?: string;
}

export interface InstrumentResult {
  filesScanned: number;
  filesChanged: number;
  detections: number;
  changes: FileChange[];
}

export interface FileChange {
  file: string;
  applied: Detection[];
  alreadyInstrumented: number;
}

export async function runInstrument(opts: InstrumentOptions = {}): Promise<InstrumentResult> {
  const cwd = opts.cwd ?? process.cwd();
  const cfg = loadConfig(cwd);
  if (!cfg) {
    throw new Error(
      `no ${pc.bold('flychain.config.json')} found in ${cwd}; run \`flychain init\` first`,
    );
  }

  const files = await discoverSourceFiles({ cwd });
  const changes: FileChange[] = [];
  let filesChanged = 0;
  let detectionsTotal = 0;

  for (const abs of files) {
    const lang = languageOf(abs);
    if (!lang) continue;
    const source = readFileSync(abs, 'utf8');
    const detections = detect(source, lang);
    if (detections.length === 0) continue;

    detectionsTotal += detections.length;
    const alreadyInstrumented = detections.filter((d) => d.alreadyInstrumented).length;

    const { source: rewritten, changed, applied } = rewrite(source, detections, cfg);
    if (opts.apply && changed) {
      writeFileSync(abs, rewritten, 'utf8');
      filesChanged += 1;
    } else if (!opts.apply && applied.length > 0) {
      filesChanged += 1;
    }

    changes.push({ file: abs, applied, alreadyInstrumented });
  }

  printSummary(changes, filesChanged, opts.apply === true, cfg, cwd);
  return {
    filesScanned: files.length,
    filesChanged,
    detections: detectionsTotal,
    changes,
  };
}

function printSummary(
  changes: FileChange[],
  filesChanged: number,
  applied: boolean,
  cfg: FlyChainProjectConfig,
  cwd: string,
): void {
  const header = applied
    ? pc.green(`✓ instrumented ${filesChanged} file(s)`)
    : pc.cyan(`preview: ${filesChanged} file(s) would be changed (run with --apply)`);
  console.log(header);

  for (const change of changes) {
    const rel = relative(cwd, change.file);
    const applyLabel =
      change.applied.length > 0 ? pc.green(`+${change.applied.length}`) : pc.dim('no-op');
    const skipped =
      change.alreadyInstrumented > 0
        ? pc.yellow(` (${change.alreadyInstrumented} already instrumented)`)
        : '';
    console.log(`  ${applyLabel} ${rel}${skipped}`);
    for (const d of change.applied) {
      console.log(pc.dim(`      ${d.kind} @ ${d.start}-${d.end}`));
    }
  }

  if (!applied) {
    console.log(pc.dim('Pass --apply to write these changes.'));
  } else {
    console.log(pc.dim(`Project ${cfg.projectId} -> ${cfg.gatewayUrl}`));
  }
}
