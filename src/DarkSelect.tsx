import { useEffect, useRef, useState } from "react";

export interface DarkSelectOption {
  value: string;
  label: string;
}

// Custom dropdown: native <select> popups ignore dark styling on some Linux webviews.
export default function DarkSelect({
  value, options, disabled, width = 180, popupWidth, ariaLabel, textColor = "#e6edf3", onChange,
}: {
  value: string;
  options: DarkSelectOption[];
  disabled?: boolean;
  width?: number | string;
  popupWidth?: number;
  ariaLabel?: string;
  textColor?: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  function toggle() {
    if (!open && ref.current) {
      const r = ref.current.getBoundingClientRect();
      const menuWidth = Math.min(popupWidth ?? r.width, window.innerWidth - 8);
      setRect({
        top: r.bottom + 2,
        left: Math.max(4, Math.min(r.left, window.innerWidth - menuWidth - 4)),
        width: menuWidth,
      });
    }
    setOpen(o => !o);
  }

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const dismiss = () => setOpen(false);
    document.addEventListener("mousedown", close);
    window.addEventListener("resize", dismiss);
    return () => {
      document.removeEventListener("mousedown", close);
      window.removeEventListener("resize", dismiss);
    };
  }, [open]);

  const selected = options.find(o => o.value === value);

  return (
    <div ref={ref} style={{ position: "relative", width, fontSize: 10 }}>
      <button
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={toggle}
        style={{
          width: "100%", textAlign: "left", padding: "2px 20px 2px 6px", borderRadius: 4,
          border: "1px solid var(--border)", color: textColor,
          backgroundColor: "#000",
          backgroundImage: `url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6'><path d='M0 0l5 6 5-6z' fill='%23e6edf3'/></svg>")`,
          backgroundRepeat: "no-repeat",
          backgroundPosition: "right 6px center",
          fontSize: 10, cursor: disabled ? "default" : "pointer",
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}
      >
        {selected?.label ?? value}
      </button>
      {open && rect && (
        <div
          role="listbox"
          style={{
            position: "fixed", top: rect.top, left: rect.left, width: rect.width, zIndex: 1000,
            background: "#000", border: "1px solid var(--border)",
            borderRadius: 4, maxHeight: 240, overflowY: "auto",
          }}
        >
          {options.map(o => (
            <div
              key={o.value}
              role="option"
              aria-selected={o.value === value}
              onClick={() => { setOpen(false); onChange(o.value); }}
              style={{
                padding: "3px 6px", cursor: "pointer", color: textColor,
                background: o.value === value ? "#1f2a37" : "#000",
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}
              onMouseEnter={e => { e.currentTarget.style.background = "#1f2a37"; }}
              onMouseLeave={e => { e.currentTarget.style.background = o.value === value ? "#1f2a37" : "#000"; }}
            >
              {o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
