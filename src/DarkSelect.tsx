import { useEffect, useRef, useState, type KeyboardEvent } from "react";

export interface DarkSelectOption {
  value: string;
  label: string;
  columns?: string[];
}

// Custom dropdown: native <select> popups ignore dark styling on some Linux webviews.
export default function DarkSelect({
  value, options, disabled, width = 180, popupWidth, ariaLabel, placeholder, columnTemplate, textColor = "#e6edf3", onChange,
}: {
  value: string;
  options: DarkSelectOption[];
  disabled?: boolean;
  width?: number | string;
  popupWidth?: number;
  ariaLabel?: string;
  placeholder?: string;
  columnTemplate?: string;
  textColor?: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [keyboardNavigation, setKeyboardNavigation] = useState(false);
  const [rect, setRect] = useState<{ top: number; left: number; width: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<(HTMLDivElement | null)[]>([]);

  function showMenu(index = Math.max(0, options.findIndex(option => option.value === value)), focusOptions = false) {
    if (ref.current) {
      const r = ref.current.getBoundingClientRect();
      const menuWidth = Math.min(popupWidth ?? r.width, window.innerWidth - 8);
      setRect({
        top: r.bottom + 2,
        left: Math.max(4, Math.min(r.left, window.innerWidth - menuWidth - 4)),
        width: menuWidth,
      });
    }
    setActiveIndex(index);
    setKeyboardNavigation(focusOptions);
    setOpen(true);
  }

  function selectOption(option: DarkSelectOption) {
    setOpen(false);
    onChange(option.value);
    buttonRef.current?.focus();
  }

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (disabled) return;
    const lastIndex = Math.max(0, options.length - 1);
    const selectedIndex = options.findIndex(option => option.value === value);
    switch (event.key) {
      case "ArrowDown":
      case "ArrowUp": {
        event.preventDefault();
        const direction = event.key === "ArrowDown" ? 1 : -1;
        if (!open) showMenu(selectedIndex >= 0 ? selectedIndex : direction > 0 ? 0 : lastIndex, true);
        else {
          setKeyboardNavigation(true);
          setActiveIndex(index => keyboardNavigation
            ? Math.max(0, Math.min(lastIndex, index + direction))
            : selectedIndex >= 0 ? selectedIndex : direction > 0 ? 0 : lastIndex);
        }
        break;
      }
      case "Home":
      case "End":
        if (!open) return;
        event.preventDefault();
        setKeyboardNavigation(true);
        setActiveIndex(event.key === "Home" ? 0 : lastIndex);
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        if (!open) showMenu(undefined, true);
        else if (options[activeIndex]) selectOption(options[activeIndex]);
        break;
      case "Escape":
        if (!open) return;
        event.preventDefault();
        event.stopPropagation();
        setOpen(false);
        buttonRef.current?.focus();
        break;
    }
  }

  useEffect(() => {
    if (!open || !keyboardNavigation) return;
    const option = optionRefs.current[activeIndex];
    option?.focus({ preventScroll: true });
    option?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex, keyboardNavigation]);

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
    <div ref={ref} style={{ position: "relative", width, fontSize: 10 }}
      onKeyDown={onKeyDown}
      onBlur={event => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}>
      <button
        ref={buttonRef}
        type="button"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => open ? setOpen(false) : showMenu()}
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
        {selected?.label ?? placeholder ?? value}
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
          {options.map((o, index) => (
            <div
              key={o.value}
              ref={element => { optionRefs.current[index] = element; }}
              role="option"
              tabIndex={-1}
              aria-selected={o.value === value}
              onFocus={() => { setActiveIndex(index); setKeyboardNavigation(true); }}
              onClick={() => selectOption(o)}
              style={{
                padding: "3px 6px", cursor: "pointer", color: textColor,
                display: o.columns ? "grid" : "block",
                gridTemplateColumns: columnTemplate, gap: o.columns ? 8 : undefined,
                fontFamily: "monospace", fontSize: 10,
                background: (keyboardNavigation && index === activeIndex) || o.value === value ? "#1f2a37" : "#000",
                whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
              }}
              onMouseEnter={e => { e.currentTarget.style.background = "#1f2a37"; }}
              onMouseLeave={e => { e.currentTarget.style.background = (keyboardNavigation && index === activeIndex) || o.value === value ? "#1f2a37" : "#000"; }}
            >
              {o.columns
                ? o.columns.map((column, index) => <span key={index}>{column}</span>)
                : o.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
