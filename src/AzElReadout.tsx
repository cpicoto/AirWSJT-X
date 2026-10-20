import type { CSSProperties, HTMLAttributes } from "react";

interface AzElReadoutProps {
  az: number;
  el: number;
  invalid: boolean;
  // Fixed font size; when omitted the text scales with the enclosing window (see .overlay-frame in index.css).
  fontPx?: number;
  className: string;
  headerProps?: HTMLAttributes<HTMLDivElement>;
}

export default function AzElReadout({ az, el, invalid, fontPx, className, headerProps }: AzElReadoutProps) {
  const dim = invalid ? { opacity: 0.35 } : undefined;
  const azText = `${Number.isFinite(az) ? az.toFixed(1) : "--.-"}°`;
  const elText = `${Number.isFinite(el) ? el.toFixed(1) : "--.-"}°`;
  const gridStyle = { "--n": Math.max(azText.length, elText.length), ...(fontPx ? { fontSize: `${fontPx}px` } : {}) } as CSSProperties;
  return (
    <div className={className}>
      <div className="portable-header" {...headerProps}>
        <span>Az / El</span>
      </div>
      <div className="portable-grid" style={gridStyle}>
        <div className="portable-readout">
          <span className="portable-label">AZ</span>
          <span className="portable-value" style={dim}>{azText}</span>
        </div>
        <div className="portable-readout">
          <span className="portable-label">EL</span>
          <span className="portable-value" style={dim}>{elText}</span>
        </div>
      </div>
    </div>
  );
}
