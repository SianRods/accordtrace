import React, {useState} from 'react';
const KEY = 'accordtrace-theme';
function initialTheme(){try {return localStorage.getItem(KEY)==='light'?'light':'dark'} catch {return 'dark'}}
export function applyTheme(theme){document.documentElement.dataset.theme=theme;try{localStorage.setItem(KEY,theme)}catch{}}
applyTheme(initialTheme());
export default function ThemeToggle(){const [theme,setTheme]=useState(initialTheme);function toggle(){const next=theme==='dark'?'light':'dark';setTheme(next);applyTheme(next)}return <button type="button" className="theme-toggle" onClick={toggle} role="switch" aria-checked={theme==='light'} aria-label="Light mode"><span className="theme-toggle-label">{theme==='light'?'Light':'Dark'}</span><span className="theme-toggle-track" aria-hidden="true"><span/></span></button>}
