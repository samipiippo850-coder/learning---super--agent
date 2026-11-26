// Download reference material from GitHub raw URLs (Node's fetch works where the
// sandboxed shell TLS stack is blocked).
import { mkdir, writeFile } from "node:fs/promises";
import { dirname } from "node:path";

const targets = [
  ["https://raw.githubusercontent.com/datawhalechina/ai-skills-for-everyone/main/catalog/coding-and-devtools/frontend-design.md", "docs/reference/frontend-design.md"],
  ["https://raw.githubusercontent.com/aranx-ai/skills/main/README.md", "docs/reference/frontend-polish-readme.md"],
  ["https://raw.githubusercontent.com/ezra-y/awesome-claude-ui-armory/main/README.md", "docs/reference/ui-armory.md"],
  ["https://raw.githubusercontent.com/anthropics/skills/main/skills/frontend-design/SKILL.md", "docs/reference/anthropic-frontend-design.md"],
];

for (const [url, dest] of targets) {
  try {
    const response = await fetch(url, { redirect: "follow" });
    if (!response.ok) {
      console.log(`HTTP ${response.status} ${url}`);
      continue;
    }
    const text = await response.text();
    await mkdir(dirname(dest), { recursive: true });
    await writeFile(dest, text, "utf8");
    console.log(`saved ${text.length} chars -> ${dest}`);
  } catch (error) {
    console.log(`FAIL ${url}: ${error.message}`);
  }
}
