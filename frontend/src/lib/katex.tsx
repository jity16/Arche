import "katex/dist/katex.min.css";
import { renderInlineMath, renderMarkdownMath } from "./markdown";

/** 把混排文本（自然语言 + $...$ / $$...$$ 公式 + 裸 \ce{}）渲染成 HTML 字符串。 */
export function renderMixed(text: string): string {
  return renderInlineMath(text);
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

/** 块级 Markdown + 公式预览，供编辑器等完整预览区域使用。 */
export function MarkdownText({ text, className }: { text: string; className?: string }) {
  const html = renderMarkdownMath(text);
  if (!html) return null;
  return (
    // biome-ignore lint/security/noDangerouslySetInnerHtml: Markdown 文本已转义，公式由 KaTeX 渲染
    <div className={`markdown-katex prose-katex ${className ?? ""}`} dangerouslySetInnerHTML={{ __html: html }} />
  );
}
