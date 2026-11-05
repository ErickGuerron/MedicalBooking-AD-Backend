// Compiles the MJML email sources into the HTML templates the Python backend renders.
//
// Usage:
//   node email-templates/build.mjs          write the compiled HTML files
//   node email-templates/build.mjs --check  fail if the committed HTML is stale
//
// Node.js is needed only to change a template. The backend never runs Node at runtime.

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import mjml2html from "mjml";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");
const outputDir = path.join(root, "src", "medicalbooking", "modules", "identity", "infrastructure", "email_templates");

const templates = [{ source: path.join(here, "activation.mjml"), output: path.join(outputDir, "activation.html") }];

const check = process.argv.includes("--check");
let stale = false;

for (const { source, output } of templates) {
  const input = await readFile(source, "utf8");
  const { html, errors } = await mjml2html(input, { validationLevel: "strict" });
  if (errors.length > 0) {
    throw new Error(`MJML errors in ${source}: ${JSON.stringify(errors)}`);
  }
  if (check) {
    const committed = await readFile(output, "utf8").catch(() => "");
    if (committed !== html) {
      console.error(`Stale template: ${path.relative(root, output)}. Run: node email-templates/build.mjs`);
      stale = true;
    }
  } else {
    await mkdir(path.dirname(output), { recursive: true });
    await writeFile(output, html, "utf8");
    console.log(`Compiled ${path.relative(root, output)}`);
  }
}

process.exit(stale ? 1 : 0);
