/** 化学装饰 SVG 库：分子结构 + 实验玻璃器皿。统一用 currentColor，便于按位置着色/调透明度。 */

type SvgProps = { className?: string };

/** 苯环（六元芳环 + 离域大 π 圈）。 */
export function BenzeneRing({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 64 64" className={className} fill="none" stroke="currentColor" strokeWidth="2.2" aria-hidden="true">
      <polygon points="32,6 53,18 53,42 32,54 11,42 11,18" strokeLinejoin="round" />
      <circle cx="32" cy="30" r="12" strokeWidth="1.5" />
    </svg>
  );
}

/** 稠环（萘式双六元环）。 */
export function FusedRings({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 96 64" className={className} fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinejoin="round" aria-hidden="true">
      <polygon points="30,8 50,20 50,44 30,56 10,44 10,20" />
      <polygon points="66,8 86,20 86,44 66,56 46,44 46,20" />
    </svg>
  );
}

/** 水分子 H–O–H（约 104.5° 弯曲）。 */
export function WaterMolecule({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 80 60" className={className} aria-hidden="true">
      <g stroke="currentColor" strokeWidth="3" strokeLinecap="round">
        <line x1="40" y1="24" x2="17" y2="46" />
        <line x1="40" y1="24" x2="63" y2="46" />
      </g>
      <g fill="currentColor">
        <circle cx="40" cy="22" r="12" />
        <circle cx="15" cy="48" r="7.5" />
        <circle cx="65" cy="48" r="7.5" />
      </g>
    </svg>
  );
}

/** 二氧化碳 O=C=O（线性，双键）。 */
export function CarbonDioxide({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 104 36" className={className} aria-hidden="true">
      <g stroke="currentColor" strokeWidth="2.4">
        <line x1="26" y1="14" x2="50" y2="14" />
        <line x1="26" y1="22" x2="50" y2="22" />
        <line x1="54" y1="14" x2="78" y2="14" />
        <line x1="54" y1="22" x2="78" y2="22" />
      </g>
      <g fill="currentColor">
        <circle cx="16" cy="18" r="10" />
        <circle cx="52" cy="18" r="9" />
        <circle cx="88" cy="18" r="10" />
      </g>
    </svg>
  );
}

/** 甲烷 CH₄（中心 C + 四个 H）。 */
export function Methane({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 72 72" className={className} aria-hidden="true">
      <g stroke="currentColor" strokeWidth="2.6" strokeLinecap="round">
        <line x1="36" y1="36" x2="36" y2="12" />
        <line x1="36" y1="36" x2="36" y2="60" />
        <line x1="36" y1="36" x2="14" y2="48" />
        <line x1="36" y1="36" x2="58" y2="48" />
      </g>
      <g fill="currentColor">
        <circle cx="36" cy="36" r="11" />
        <circle cx="36" cy="10" r="6" />
        <circle cx="36" cy="62" r="6" />
        <circle cx="12" cy="49" r="6" />
        <circle cx="60" cy="49" r="6" />
      </g>
    </svg>
  );
}

/** 原子电子轨道（核 + 三条轨道 + 电子）。 */
export function AtomOrbits({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="1.8">
        <ellipse cx="32" cy="32" rx="28" ry="11" />
        <ellipse cx="32" cy="32" rx="28" ry="11" transform="rotate(60 32 32)" />
        <ellipse cx="32" cy="32" rx="28" ry="11" transform="rotate(120 32 32)" />
      </g>
      <g fill="currentColor">
        <circle cx="32" cy="32" r="5" />
        <circle cx="60" cy="32" r="2.6" />
        <circle cx="18" cy="56" r="2.6" />
        <circle cx="18" cy="8" r="2.6" />
      </g>
    </svg>
  );
}

/** 锥形瓶（Erlenmeyer）+ 液体 + 上升气泡。 */
export function Flask({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 80 100" className={className} aria-hidden="true">
      <defs>
        <clipPath id="flask-body">
          <path d="M32 8 h16 v30 l20 44 a6 6 0 0 1 -5.5 8 h-45 a6 6 0 0 1 -5.5 -8 l20 -44 Z" />
        </clipPath>
      </defs>
      {/* 液体 */}
      <g clipPath="url(#flask-body)">
        <rect x="0" y="64" width="80" height="36" className="fill-emerald-300/45" />
        <circle cx="30" cy="84" r="2.4" className="fill-white/70 anim-bubble" style={{ animationDelay: "0s" }} />
        <circle cx="44" cy="88" r="3" className="fill-white/70 anim-bubble" style={{ animationDelay: "0.7s" }} />
        <circle cx="52" cy="82" r="2" className="fill-white/70 anim-bubble" style={{ animationDelay: "1.3s" }} />
        <circle cx="38" cy="90" r="2.6" className="fill-white/70 anim-bubble" style={{ animationDelay: "1.9s" }} />
      </g>
      {/* 瓶身轮廓 + 瓶口 */}
      <g fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round">
        <path d="M32 8 h16 v30 l20 44 a6 6 0 0 1 -5.5 8 h-45 a6 6 0 0 1 -5.5 -8 l20 -44 Z" />
        <line x1="30" y1="8" x2="50" y2="8" />
        <line x1="24" y1="64" x2="56" y2="64" strokeWidth="2" strokeDasharray="3 3" opacity="0.6" />
      </g>
    </svg>
  );
}

/** 烧杯 + 刻度 + 液体。 */
export function Beaker({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 72 88" className={className} aria-hidden="true">
      <defs>
        <clipPath id="beaker-body">
          <path d="M14 14 h44 v60 a6 6 0 0 1 -6 6 h-32 a6 6 0 0 1 -6 -6 Z" />
        </clipPath>
      </defs>
      <g clipPath="url(#beaker-body)">
        <rect x="0" y="48" width="72" height="40" className="fill-emerald-300/45" />
      </g>
      <g fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round">
        <path d="M10 10 h52 M14 14 v60 a6 6 0 0 0 6 6 h32 a6 6 0 0 0 6 -6 v-60" />
      </g>
      <g stroke="currentColor" strokeWidth="1.6" opacity="0.5">
        <line x1="50" y1="30" x2="58" y2="30" />
        <line x1="50" y1="44" x2="58" y2="44" />
        <line x1="50" y1="58" x2="58" y2="58" />
      </g>
    </svg>
  );
}

/** 试管 + 液体。 */
export function TestTube({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 36 96" className={className} aria-hidden="true">
      <defs>
        <clipPath id="tube-body">
          <path d="M11 6 v66 a7 7 0 0 0 14 0 v-66 Z" />
        </clipPath>
      </defs>
      <g clipPath="url(#tube-body)">
        <rect x="0" y="46" width="36" height="50" className="fill-amber-300/45" />
      </g>
      <g fill="none" stroke="currentColor" strokeWidth="3" strokeLinejoin="round" strokeLinecap="round">
        <path d="M8 6 h20 M11 6 v66 a7 7 0 0 0 14 0 v-66" />
      </g>
    </svg>
  );
}

/** 化学反应箭头（带催化条件位）。 */
export function ReactionArrow({ className = "" }: SvgProps) {
  return (
    <svg viewBox="0 0 96 24" className={className} fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
      <line x1="6" y1="16" x2="84" y2="16" />
      <polyline points="74,9 86,16 74,23" strokeLinejoin="round" />
      <line x1="20" y1="16" x2="34" y2="16" strokeWidth="3" opacity="0" />
    </svg>
  );
}
