import "./styles.css";
import "./theme.css";
export const metadata = { title: "MedFlow AI", description: "Мониторинг госпитализации и поддержка управленческих решений" };
const themeScript = `(function(){try{var p=localStorage.getItem('medflow-theme')||'system';var d=p==='dark'||(p==='system'&&matchMedia('(prefers-color-scheme: dark)').matches)?'dark':'light';document.documentElement.dataset.theme=d;document.documentElement.style.colorScheme=d}catch(e){}})()`;
export default function Layout({ children }) { return <html lang="ru" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{__html:themeScript}} /></head><body>{children}</body></html>; }
