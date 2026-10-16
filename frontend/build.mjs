import { build } from 'esbuild';
import { copyFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

const output = new URL('../app/static/', import.meta.url);
await mkdir(output, { recursive: true });
await build({
  entryPoints: [fileURLToPath(new URL('demo.js', import.meta.url))],
  outfile: fileURLToPath(new URL('demo.js', output)),
  bundle: true,
  minify: true,
  format: 'esm',
  target: ['chrome120', 'firefox121', 'safari17'],
});
await copyFile(new URL('node_modules/htmx.org/dist/htmx.min.js', import.meta.url), new URL('htmx.min.js', output));
