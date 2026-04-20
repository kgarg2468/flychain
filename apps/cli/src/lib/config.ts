/**
 * Read / write the local FlyChain project config file.
 *
 * File: `flychain.config.json` at the repo root. JSON rather than TS so the
 * gateway, orchestrator, and dashboard can all read it without a bundler.
 */
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';

export const CONFIG_FILENAME = 'flychain.config.json';

export interface FlyChainProjectConfig {
  projectId: string;
  gatewayUrl: string;
  tags: Record<string, string>;
  providers: ('openai' | 'anthropic' | 'local-ollama')[];
  capabilities: string[];
  version: 1;
}

export const DEFAULT_CONFIG: Omit<FlyChainProjectConfig, 'projectId'> = {
  gatewayUrl: 'http://localhost:8080',
  tags: {},
  providers: ['openai', 'anthropic', 'local-ollama'],
  capabilities: [],
  version: 1,
};

export function configPath(cwd: string = process.cwd()): string {
  return resolve(cwd, CONFIG_FILENAME);
}

export function loadConfig(cwd: string = process.cwd()): FlyChainProjectConfig | null {
  const path = configPath(cwd);
  if (!existsSync(path)) return null;
  const raw = readFileSync(path, 'utf8');
  return JSON.parse(raw) as FlyChainProjectConfig;
}

export function saveConfig(cfg: FlyChainProjectConfig, cwd: string = process.cwd()): string {
  const path = configPath(cwd);
  writeFileSync(path, JSON.stringify(cfg, null, 2) + '\n', 'utf8');
  return path;
}

export function buildConfig(
  projectId: string,
  overrides: Partial<FlyChainProjectConfig> = {},
): FlyChainProjectConfig {
  return {
    ...DEFAULT_CONFIG,
    projectId,
    ...overrides,
  };
}
