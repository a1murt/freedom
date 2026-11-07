import * as React from "react";
import { Mascot } from "@/components/Mascot";

interface State {
  error: Error | null;
}

/** Last-resort boundary so a rendering bug in one screen never blanks the whole app. */
export class ErrorBoundary extends React.Component<{ children: React.ReactNode }, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[CQA] render error", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
          <Mascot pose="oops" size={120} />
          <div className="text-h3 font-display">Something went wrong</div>
          <pre className="max-w-xl whitespace-pre-wrap rounded-sm border border-line bg-surface px-3 py-2 text-left font-mono text-[12px] text-text-2">{this.state.error.message}</pre>
          <button type="button" className="rounded-sm border border-line bg-surface px-3 py-1.5 text-caption hover:bg-surface-2" onClick={() => this.setState({ error: null })}>
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
