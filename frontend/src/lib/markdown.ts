import katex from "katex";
import "katex/dist/contrib/mhchem.mjs";

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function escapeText(s: string): string {
  return escapeHtml(s).replace(/\n/g, "<br/>");
}

function renderFormula(source: string, displayMode: boolean): string {
  try {
    return katex.renderToString(source, { throwOnError: false, displayMode });
  } catch {
    return escapeText(source);
  }
}

function renderToken(token: string): string | null {
  if (token.startsWith("$$") && token.endsWith("$$") && token.length >= 4) {
    return renderFormula(token.slice(2, -2), true);
  }
  if (token.startsWith("$") && token.endsWith("$") && token.length >= 2) {
    return renderFormula(token.slice(1, -1), false);
  }
  if (/^\\(?:ce|pu)\{[^{}]*\}$/.test(token)) {
    return renderFormula(token, false);
  }
  return null;
}

function renderWithFormulaTokens(text: string, renderPlain: (part: string) => string): string {
  const parts = text.split(/(\$\$[\s\S]*?\$\$|\$[^$\n]*?\$|\\(?:ce|pu)\{[^{}]*\})/g);
  return parts
    .map((part) => {
      const rendered = renderToken(part);
      return rendered ?? renderPlain(part);
    })
    .join("");
}

function renderInlineMarkdown(part: string): string {
  return part
    .split(/(`[^`\n]*`)/g)
    .map((segment) => {
      if (segment.startsWith("`") && segment.endsWith("`")) {
        return `<code>${escapeHtml(segment.slice(1, -1))}</code>`;
      }

      return escapeHtml(segment)
        .replace(
          /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g,
          '<a href="$2" target="_blank" rel="noreferrer">$1</a>',
        )
        .replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    })
    .join("");
}

function renderInlineMarkdownMath(text: string): string {
  return renderWithFormulaTokens(text, renderInlineMarkdown);
}

export function renderInlineMath(text: string): string {
  if (!text || !text.trim()) return "";
  return renderWithFormulaTokens(text, escapeText);
}

function isUnorderedList(line: string): boolean {
  return /^\s*[-*+]\s+/.test(line);
}

function isOrderedList(line: string): boolean {
  return /^\s*\d+[.)]\s+/.test(line);
}

function isBlockStart(line: string): boolean {
  const trimmed = line.trim();
  return (
    !trimmed ||
    trimmed.startsWith("```") ||
    trimmed.startsWith("$$") ||
    /^#{1,6}\s+/.test(trimmed) ||
    /^>\s?/.test(trimmed) ||
    isUnorderedList(line) ||
    isOrderedList(line)
  );
}

export function renderMarkdownMath(text: string): string {
  if (!text || !text.trim()) return "";

  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const blocks: string[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    if (trimmed.startsWith("```")) {
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith("```")) {
        code.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push(`<pre><code>${escapeHtml(code.join("\n"))}</code></pre>`);
      continue;
    }

    if (trimmed.startsWith("$$")) {
      const math: string[] = [];
      if (trimmed.endsWith("$$") && trimmed.length > 2) {
        math.push(trimmed.slice(2, -2));
        i += 1;
      } else {
        math.push(trimmed.slice(2));
        i += 1;
        while (i < lines.length && !lines[i].trim().endsWith("$$")) {
          math.push(lines[i]);
          i += 1;
        }
        if (i < lines.length) {
          math.push(lines[i].trim().slice(0, -2));
          i += 1;
        }
      }
      blocks.push(`<div class="markdown-math-block">${renderFormula(math.join("\n"), true)}</div>`);
      continue;
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(trimmed);
    if (heading) {
      const level = heading[1].length;
      blocks.push(`<h${level}>${renderInlineMarkdownMath(heading[2])}</h${level}>`);
      i += 1;
      continue;
    }

    if (isUnorderedList(line) || isOrderedList(line)) {
      const ordered = isOrderedList(line);
      const tag = ordered ? "ol" : "ul";
      const items: string[] = [];
      while (i < lines.length && (ordered ? isOrderedList(lines[i]) : isUnorderedList(lines[i]))) {
        const item = lines[i].replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*+]\s+/, "");
        items.push(`<li>${renderInlineMarkdownMath(item)}</li>`);
        i += 1;
      }
      blocks.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      const quoted: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
        quoted.push(lines[i].trim().replace(/^>\s?/, ""));
        i += 1;
      }
      blocks.push(`<blockquote>${renderMarkdownMath(quoted.join("\n"))}</blockquote>`);
      continue;
    }

    const paragraph: string[] = [];
    while (i < lines.length && !isBlockStart(lines[i])) {
      paragraph.push(lines[i].trim());
      i += 1;
    }
    if (!paragraph.length) {
      paragraph.push(trimmed);
      i += 1;
    }
    blocks.push(`<p>${renderInlineMarkdownMath(paragraph.join(" "))}</p>`);
  }

  return blocks.join("");
}
