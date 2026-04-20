/**
 * Detection + rewrite of OpenAI / Anthropic client constructions.
 *
 * We match the following shapes:
 *
 *   TS / JS:
 *     new OpenAI({ ... })
 *     OpenAI({ ... })
 *     new Anthropic({ ... })
 *     Anthropic({ ... })
 *
 *   Python:
 *     OpenAI(...)
 *     openai.OpenAI(...)
 *     Anthropic(...)
 *     anthropic.Anthropic(...)
 *
 * The rewriter produces a minimal diff: it injects the FlyChain gateway as
 * the client's base URL and adds FlyChain project headers to the default
 * headers map. Constructors that already point at a FlyChain gateway are
 * skipped.
 *
 * This is regex-based rather than full-AST. That keeps the CLI dependency
 * surface small and is sufficient for the common library shapes. Unusual
 * call sites can be handled with `--agent` in a later iteration.
 */
import type { FlyChainProjectConfig } from './config.js';

export type Language = 'python' | 'typescript';
export type ClientKind = 'openai' | 'anthropic';

export interface Detection {
  language: Language;
  kind: ClientKind;
  /** Start index in source (inclusive) of the constructor call. */
  start: number;
  /** End index in source (exclusive) of the matched call. */
  end: number;
  /** The matched substring (for diagnostics). */
  matched: string;
  /** True if the match already references flychain / localhost:8080. */
  alreadyInstrumented: boolean;
}

const TS_PATTERNS: { kind: ClientKind; re: RegExp }[] = [
  { kind: 'openai', re: /\bnew\s+OpenAI\s*\(([\s\S]*?)\)/g },
  { kind: 'openai', re: /(?<![A-Za-z0-9_$.])OpenAI\s*\(([\s\S]*?)\)/g },
  { kind: 'anthropic', re: /\bnew\s+Anthropic\s*\(([\s\S]*?)\)/g },
  { kind: 'anthropic', re: /(?<![A-Za-z0-9_$.])Anthropic\s*\(([\s\S]*?)\)/g },
];

const PY_PATTERNS: { kind: ClientKind; re: RegExp }[] = [
  { kind: 'openai', re: /(?<![A-Za-z0-9_])(?:openai\.)?OpenAI\s*\(([\s\S]*?)\)/g },
  { kind: 'anthropic', re: /(?<![A-Za-z0-9_])(?:anthropic\.)?Anthropic\s*\(([\s\S]*?)\)/g },
];

export function languageOf(path: string): Language | null {
  if (path.endsWith('.py')) return 'python';
  if (/\.(ts|tsx|mts|cts|js|jsx|mjs|cjs)$/i.test(path)) return 'typescript';
  return null;
}

export function detect(source: string, language: Language): Detection[] {
  const patterns = language === 'python' ? PY_PATTERNS : TS_PATTERNS;
  const raw: Detection[] = [];

  for (const { kind, re } of patterns) {
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(source)) !== null) {
      const start = m.index;
      const end = m.index + m[0].length;
      const matched = m[0];
      const alreadyInstrumented = /flychain|localhost:8080|x-flychain-/i.test(matched);
      raw.push({ language, kind, start, end, matched, alreadyInstrumented });
    }
  }

  raw.sort((a, b) => {
    if (a.end !== b.end) return a.end - b.end;
    return a.start - b.start;
  });

  // Collapse overlapping matches: keep the longer one (which is the `new X(` /
  // namespaced form) and drop the shorter bare match nested inside it.
  const accepted: Detection[] = [];
  for (const d of raw) {
    const overlap = accepted.findIndex((a) => a.end === d.end && a.kind === d.kind);
    if (overlap !== -1) {
      const a = accepted[overlap]!;
      if (d.end - d.start > a.end - a.start) accepted[overlap] = d;
      continue;
    }
    accepted.push(d);
  }

  accepted.sort((a, b) => a.start - b.start);
  return accepted;
}

export interface RewriteResult {
  source: string;
  changed: boolean;
  applied: Detection[];
}

export function rewrite(
  source: string,
  detections: Detection[],
  cfg: FlyChainProjectConfig,
): RewriteResult {
  if (detections.length === 0) {
    return { source, changed: false, applied: [] };
  }

  const applied: Detection[] = [];
  // Apply in reverse so earlier indexes stay valid.
  let out = source;
  const ordered = [...detections].sort((a, b) => b.start - a.start);

  for (const d of ordered) {
    if (d.alreadyInstrumented) continue;
    const original = out.slice(d.start, d.end);
    const replacement = rewriteCall(original, d, cfg);
    if (replacement === original) continue;
    out = out.slice(0, d.start) + replacement + out.slice(d.end);
    applied.push(d);
  }

  applied.sort((a, b) => a.start - b.start);
  return { source: out, changed: applied.length > 0, applied };
}

function rewriteCall(original: string, d: Detection, cfg: FlyChainProjectConfig): string {
  const openParen = original.indexOf('(');
  if (openParen === -1) return original;
  const closeParen = original.lastIndexOf(')');
  if (closeParen === -1 || closeParen <= openParen) return original;

  const head = original.slice(0, openParen + 1);
  const tail = original.slice(closeParen);
  const inner = original.slice(openParen + 1, closeParen);

  if (d.language === 'typescript') {
    return head + rewriteTsInner(inner, d.kind, cfg) + tail;
  }
  return head + rewritePyInner(inner, d.kind, cfg) + tail;
}

function rewriteTsInner(inner: string, kind: ClientKind, cfg: FlyChainProjectConfig): string {
  const trimmed = inner.trim();
  const headerEntries: string[] = [`    'x-flychain-project': '${jsonEscape(cfg.projectId)}'`];
  for (const [k, v] of Object.entries(cfg.tags)) {
    headerEntries.push(
      `    'x-flychain-tags-${jsonEscape(k)}': '${jsonEscape(v)}'`, // informational
    );
  }
  const headersBlock = `{
${headerEntries.join(',\n')}
  }`;

  const baseUrlKey = kind === 'anthropic' ? 'baseURL' : 'baseURL';
  const headersKey = kind === 'anthropic' ? 'defaultHeaders' : 'defaultHeaders';
  const flyFields = `${baseUrlKey}: '${cfg.gatewayUrl}',
  ${headersKey}: ${headersBlock}`;

  if (trimmed === '') {
    return `{\n  ${flyFields},\n}`;
  }

  // Already an object literal: insert fields after the opening brace.
  if (trimmed.startsWith('{')) {
    const openIdx = inner.indexOf('{');
    return inner.slice(0, openIdx + 1) + `\n  ${flyFields},` + inner.slice(openIdx + 1);
  }

  // Variable reference or spread - wrap it in an object with overrides.
  return `{ ...${trimmed}, ${flyFields} }`;
}

function rewritePyInner(inner: string, kind: ClientKind, cfg: FlyChainProjectConfig): string {
  const trimmed = inner.trim();
  const headerEntries: string[] = [`    "x-flychain-project": "${pyEscape(cfg.projectId)}"`];
  for (const [k, v] of Object.entries(cfg.tags)) {
    headerEntries.push(`    "x-flychain-tags-${pyEscape(k)}": "${pyEscape(v)}"`);
  }
  const headersBlock = `{
${headerEntries.join(',\n')}
  }`;

  const baseKey = kind === 'anthropic' ? 'base_url' : 'base_url';
  const headersKey = kind === 'anthropic' ? 'default_headers' : 'default_headers';
  const flyFields = `${baseKey}="${cfg.gatewayUrl}", ${headersKey}=${headersBlock}`;

  if (trimmed === '') {
    return `${flyFields}`;
  }

  return `${trimmed.replace(/\s+$/, '')}, ${flyFields}`;
}

function jsonEscape(s: string): string {
  return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function pyEscape(s: string): string {
  return s.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}
