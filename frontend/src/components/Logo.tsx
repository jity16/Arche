/** 苯环（六元环）品牌标志。 */
export function BenzeneLogo({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} aria-hidden="true">
      <g fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinejoin="round">
        <polygon points="24,5 40,14.5 40,33.5 24,43 8,33.5 8,14.5" />
        <circle cx="24" cy="24" r="9" strokeWidth="1.6" strokeDasharray="3 3" />
      </g>
      <g fill="currentColor">
        <circle cx="24" cy="5" r="2.6" />
        <circle cx="40" cy="14.5" r="2.6" />
        <circle cx="40" cy="33.5" r="2.6" />
        <circle cx="24" cy="43" r="2.6" />
        <circle cx="8" cy="33.5" r="2.6" />
        <circle cx="8" cy="14.5" r="2.6" />
      </g>
    </svg>
  );
}

/** 旋转电子轨道 —— 运行中的 loading 指示。 */
export function AtomSpinner({ className = "" }: { className?: string }) {
  return (
    <svg viewBox="0 0 48 48" className={className} aria-hidden="true">
      <g className="arche-orbit" fill="none" stroke="currentColor" strokeWidth="2">
        <ellipse cx="24" cy="24" rx="20" ry="8" />
        <ellipse cx="24" cy="24" rx="20" ry="8" transform="rotate(60 24 24)" />
        <ellipse cx="24" cy="24" rx="20" ry="8" transform="rotate(120 24 24)" />
      </g>
      <circle cx="24" cy="24" r="4" fill="currentColor" />
    </svg>
  );
}
