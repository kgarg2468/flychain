/**
 * File discovery for `flychain instrument`.
 *
 * Globs Python + TS/JS source under a given path while excluding the usual
 * noise (node_modules, .venv, build outputs, etc).
 */
import fg from 'fast-glob';

const DEFAULT_INCLUDES = [
  '**/*.py',
  '**/*.ts',
  '**/*.tsx',
  '**/*.mts',
  '**/*.cts',
  '**/*.js',
  '**/*.jsx',
  '**/*.mjs',
  '**/*.cjs',
];

const DEFAULT_IGNORES = [
  '**/node_modules/**',
  '**/.venv/**',
  '**/venv/**',
  '**/env/**',
  '**/.next/**',
  '**/dist/**',
  '**/build/**',
  '**/out/**',
  '**/.turbo/**',
  '**/coverage/**',
  '**/__pycache__/**',
  '**/.git/**',
  '**/site-packages/**',
];

export interface DiscoverOptions {
  cwd: string;
  extraIgnores?: string[];
}

export async function discoverSourceFiles(opts: DiscoverOptions): Promise<string[]> {
  return fg(DEFAULT_INCLUDES, {
    cwd: opts.cwd,
    absolute: true,
    dot: false,
    onlyFiles: true,
    followSymbolicLinks: false,
    ignore: [...DEFAULT_IGNORES, ...(opts.extraIgnores ?? [])],
  });
}
