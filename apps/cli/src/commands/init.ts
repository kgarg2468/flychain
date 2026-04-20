/** `flychain init` command. */
import { existsSync } from 'node:fs';
import pc from 'picocolors';

import { buildConfig, configPath, saveConfig } from '../lib/config.js';

export interface InitOptions {
  projectId?: string;
  gatewayUrl?: string;
  force?: boolean;
  cwd?: string;
  tag?: string[];
}

export function parseTags(values: string[] | undefined): Record<string, string> {
  const tags: Record<string, string> = {};
  if (!values) return tags;
  for (const raw of values) {
    const idx = raw.indexOf('=');
    if (idx <= 0) continue;
    const k = raw.slice(0, idx).trim();
    const v = raw.slice(idx + 1).trim();
    if (k) tags[k] = v;
  }
  return tags;
}

export function defaultProjectId(cwd: string): string {
  const base = cwd.split(/[\\/]/).filter(Boolean).pop() ?? 'project';
  return base.toLowerCase().replace(/[^a-z0-9-]+/g, '-');
}

export async function runInit(opts: InitOptions = {}): Promise<{
  path: string;
  created: boolean;
}> {
  const cwd = opts.cwd ?? process.cwd();
  const target = configPath(cwd);
  const exists = existsSync(target);
  if (exists && !opts.force) {
    throw new Error(`${target} already exists; pass --force to overwrite`);
  }

  const projectId = opts.projectId ?? defaultProjectId(cwd);
  const gatewayUrl = opts.gatewayUrl ?? 'http://localhost:8080';
  const cfg = buildConfig(projectId, {
    gatewayUrl,
    tags: parseTags(opts.tag),
  });
  const path = saveConfig(cfg, cwd);

  console.log(pc.green('✓') + ' wrote ' + pc.bold(path));
  console.log(pc.dim(`  projectId: ${projectId}`));
  console.log(pc.dim(`  gatewayUrl: ${gatewayUrl}`));

  return { path, created: !exists };
}
