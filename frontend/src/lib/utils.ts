/** 轻量 className 合并（shadcn 组件依赖的 cn 工具的精简实现）。 */
export function cn(...inputs: Array<string | false | null | undefined>): string {
  return inputs.filter(Boolean).join(" ");
}
