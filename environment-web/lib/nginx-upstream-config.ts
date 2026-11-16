import type { ArchivedConfig } from './dns-downstream.ts';

type Token = { value: string; start: number; end: number };
export type UpstreamDefinition = { path: string; content: string };

// Retain source offsets while ignoring Nginx comments and quoted/escaped braces.
function tokenize(source: string): { tokens: Token[]; comments: [number, number][] } | null {
  const tokens: Token[] = [], comments: [number, number][] = [];
  let i = 0;
  while (i < source.length) {
    if (/\s/.test(source[i])) { i++; continue; }
    if (source[i] === '#') {
      const start = i; while (i < source.length && source[i] !== '\n') i++;
      comments.push([start, i]); continue;
    }
    const start = i;
    if ('{};'.includes(source[i])) { tokens.push({ value: source[i], start, end: ++i }); continue; }
    let value = '', quote = '';
    while (i < source.length) {
      const c = source[i];
      if (c === '\\' && i + 1 < source.length) { value += source.slice(i, i + 2); i += 2; continue; }
      if (quote) { if (c === quote) quote = ''; else value += c; i++; continue; }
      if (c === '"' || c === "'") { quote = c; i++; continue; }
      if (c === '$' && source[i + 1] === '{') {
        const end = source.indexOf('}', i + 2); if (end < 0) return null;
        value += source.slice(i, end + 1); i = end + 1; continue;
      }
      if (/\s/.test(c) || '{};'.includes(c)) break;
      value += c; i++;
    }
    if (quote) return null;
    tokens.push({ value, start, end: i });
  }
  return { tokens, comments };
}

/** Candidate definitions from the same collected instance; never guess an unknown instance. */
export function findUpstreamDefinitions(files: ArchivedConfig[], instance: string, name: string): UpstreamDefinition[] {
  if (!instance || !name) return [];
  const results: UpstreamDefinition[] = [];
  for (const file of files.filter(f => f.instance === instance && typeof f.content === 'string')) {
    const parsed = tokenize(file.content); if (!parsed) continue;
    const { tokens, comments } = parsed;
    for (let i = 0; i + 2 < tokens.length; i++) {
      if (tokens[i].value !== 'upstream' || tokens[i + 1].value !== name || tokens[i + 2].value !== '{') continue;
      if (i > 0 && ![';', '{', '}'].includes(tokens[i - 1].value)) continue;
      let depth = 1, end = i + 3;
      while (end < tokens.length && depth) { if (tokens[end].value === '{') depth++; else if (tokens[end].value === '}') depth--; end++; }
      if (depth) continue;
      const start = tokens[i].start, stop = tokens[end - 1].end;
      let content = file.content.slice(start, stop);
      for (const [a, b] of comments.slice().reverse()) if (a >= start && b <= stop) content = content.slice(0, a - start) + content.slice(b - start);
      content = content.split('\n').filter(line => line.trim()).join('\n');
      results.push({ path: file.path, content });
      i = end - 1;
    }
  }
  return results;
}
