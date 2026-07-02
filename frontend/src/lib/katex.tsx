import katex from "katex";
import "katex/dist/katex.min.css";
import "katex/dist/contrib/mhchem.mjs"; // 注册 \ce{} \pu{} —— 化学式 / 物理量单位

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;")
    .replace(/\n/g, "<br/>");
}

/** 渲染非 $...$ 文本:把裸 \ce{...} / \pu{...} 也当公式渲染(LLM/后端文本常省略 $ 包裹),其余按普通文本转义。 */
function renderPlainWithChem(part: string): string {
  const segs = part.split(/(\\ce\{[^{}]*\}|\\pu\{[^{}]*\})/g);
  return segs
    .map((seg) => {
      if (/^\\(?:ce|pu)\{[^{}]*\}$/.test(seg)) {
        try {
          return katex.renderToString(seg, { throwOnError: false, displayMode: false });
        } catch {
          return escapeHtml(seg);
        }
      }
      return escapeHtml(seg);
    })
    .join("");
}

/** 把混排文本（自然语言 + $...$ / $$...$$ 公式 + 裸 \ce{}）渲染成 HTML 字符串。 */
export function renderMixed(text: string): string {
  if (!text || !text.trim()) return "";
  const parts = text.split(/(\$\$[^$]*\$\$|\$[^$]*\$)/g);
  return parts
    .map((part) => {
      try {
        if (part.startsWith("$$") && part.endsWith("$$") && part.length >= 4) {
          return katex.renderToString(part.slice(2, -2), { throwOnError: false, displayMode: true });
        }
        if (part.startsWith("$") && part.endsWith("$") && part.length >= 2) {
          return katex.renderToString(part.slice(1, -1), { throwOnError: false, displayMode: false });
        }
      } catch {
        return renderPlainWithChem(part);
      }
      return renderPlainWithChem(part);
    })
    .join("");
}

/** 渲染含数学/化学公式的混排文本，结果区/检索摘录/输入预览复用同一渲染器。 */
export function MathText({ text, className }: { text: string; className?: string }) {
  const html = renderMixed(text);
  if (!html) return null;
  return (
    // biome-ignore lint/security/noDangerouslySetInnerHtml: KaTeX 渲染产物 + 普通文本已转义
    <span className={`prose-katex ${className ?? ""}`} dangerouslySetInnerHTML={{ __html: html }} />
  );
}
