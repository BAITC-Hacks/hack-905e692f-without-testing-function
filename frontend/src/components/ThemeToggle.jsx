"use client";

import {useEffect, useState} from "react";

const STORAGE_KEY = "medflow-theme";
const choices = [
  ["light", "Светлая"],
  ["dark", "Тёмная"],
  ["system", "Системная"],
];

function resolveTheme(preference) {
  return preference === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : preference;
}

export function ThemeToggle({compact = false}) {
  const [preference, setPreference] = useState("system");

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE_KEY);
    const initial = choices.some(([value]) => value === saved) ? saved : "system";
    setPreference(initial);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const current = localStorage.getItem(STORAGE_KEY) || "system";
      document.documentElement.dataset.theme = resolveTheme(current);
      document.documentElement.style.colorScheme = resolveTheme(current);
    };
    apply();
    media.addEventListener("change", apply);
    requestAnimationFrame(() => document.documentElement.classList.add("theme-ready"));
    return () => media.removeEventListener("change", apply);
  }, []);

  const update = (value) => {
    setPreference(value);
    localStorage.setItem(STORAGE_KEY, value);
    document.documentElement.dataset.theme = resolveTheme(value);
    document.documentElement.style.colorScheme = resolveTheme(value);
  };

  return <label className={`theme-control ${compact ? "theme-control-compact" : ""}`}>
    <span aria-hidden="true">◐</span>
    <span className="sr-only">Тема интерфейса</span>
    <select aria-label="Тема интерфейса" value={preference} onChange={(event) => update(event.target.value)}>
      {choices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select>
  </label>;
}
