import { memo } from "react";
import { AtomOrbits, Beaker, BenzeneRing, CarbonDioxide, Flask, FusedRings, Methane, TestTube, WaterMolecule } from "./Molecules";

/**
 * 全屏化学装饰层：分子结构 + 玻璃器皿，低透明度，置于内容之下（-z-10）。
 * memo 包裹（无 props）：这是全屏 fixed SVG 层，绝不能随 App 的每次 setState（启动拉取 /
 * 运行时流式事件推送）重渲染 —— 否则浏览器每次重新合成整层 → 表现为"接口推送数据后整页闪烁"。
 * memo 后只渲染一次、永不重渲染。pointer-events-none 不拦截交互。
 */
export const ChemDecor = memo(function ChemDecor() {
  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0 -z-10 overflow-hidden">
      <BenzeneRing className="anim-float absolute left-[3%] top-[12%] size-24 text-slate-500/10" />
      <WaterMolecule className="anim-float-2 absolute right-[5%] top-[16%] w-28 text-emerald-800/10" />
      <AtomOrbits className="anim-spin-slow absolute right-[8%] top-[46%] size-28 text-slate-500/10" />
      <CarbonDioxide className="anim-drift absolute left-[6%] top-[52%] w-32 text-amber-600/10" />
      <FusedRings className="anim-float absolute right-[12%] bottom-[20%] w-32 text-emerald-800/10" />
      <Methane className="anim-float-2 absolute left-[10%] bottom-[12%] size-24 text-slate-500/10" />

      {/* 玻璃器皿沉在左右下角，像摆在实验台上 */}
      <Flask className="anim-float absolute -left-2 bottom-0 h-48 text-slate-700/12" />
      <Beaker className="anim-float-2 absolute right-[2%] bottom-[2%] h-40 text-emerald-900/10" />
      <TestTube className="anim-drift absolute left-[16%] top-[24%] h-32 text-amber-700/10" />
    </div>
  );
});
