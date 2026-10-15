import { Component, StrictMode, type ErrorInfo, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles.css";

const savedTheme = window.localStorage.getItem("northstar-app-theme");
if (savedTheme === "dark" || savedTheme === "light") document.documentElement.dataset.theme = savedTheme;

class AppErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) { return { error }; }
  componentDidCatch(error: Error, info: ErrorInfo) { console.error("Northstar UI crashed", error, info); }
  render() {
    if (this.state.error) return <main className="auth-shell"><section className="auth-card" role="alert"><div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE UI</div><h1>The workspace could not render</h1><p>Refresh the page. If the problem continues, restart the local services and review the browser console.</p><details><summary>Technical detail</summary><code>{this.state.error.message}</code></details></section></main>;
    return this.props.children;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppErrorBoundary><App /></AppErrorBoundary>
  </StrictMode>,
);
