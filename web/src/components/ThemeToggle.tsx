import { useState, type ReactNode } from "react";
import { applyTheme, storedTheme, type Theme } from "../theme";

const OPTIONS: { value: Theme; label: string; icon: ReactNode }[] = [
  {
    value: "light",
    label: "Light theme",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    ),
  },
  {
    value: "dark",
    label: "Dark theme",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round">
        <path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" />
      </svg>
    ),
  },
  {
    value: "system",
    label: "Match system theme",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <rect x="3" y="4" width="18" height="12" rx="2" />
        <path d="M8 20h8M12 16v4" />
      </svg>
    ),
  },
];

export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(storedTheme);

  const choose = (t: Theme) => {
    applyTheme(t);
    setTheme(t);
  };

  return (
    <div className="theme-switch" role="group" aria-label="Colour theme">
      {OPTIONS.map((o) => (
        <button key={o.value} type="button" aria-pressed={theme === o.value}
                aria-label={o.label} title={o.label} onClick={() => choose(o.value)}>
          {o.icon}
        </button>
      ))}
    </div>
  );
}
