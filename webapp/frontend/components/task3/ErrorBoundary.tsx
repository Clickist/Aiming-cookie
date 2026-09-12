"use client";

import { Component, type ReactNode } from "react";

import { Button, ErrorState } from "@/ui/primitives";

type ErrorBoundaryProps = {
  children: ReactNode;
  /** 重试时透传给 fallback 的回调（预留，可选） */
  label?: string;
};

type ErrorBoundaryState = {
  hasError: boolean;
};

/**
 * 组件树级错误边界：接住子树渲染期异常，避免整窗白屏。
 * 接不住事件回调与异步任务里的错误（React 边界机制本身不覆盖）。
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: Error): void {
    // 保留错误与栈，方便用户截图报障。
    console.error(error);
  }

  private readonly retry = () => {
    this.setState({ hasError: false });
  };

  render() {
    if (this.state.hasError) {
      return (
        <div
          className="task3-error-boundary"
          data-error-boundary="true"
          style={{
            alignItems: "center",
            background: "var(--background)",
            color: "var(--on-surface-variant)",
            display: "grid",
            minHeight: "100%",
            padding: "var(--space-6)",
            placeItems: "center",
          }}
        >
          <ErrorState title="界面出了点问题">
            <p style={{ margin: "0 0 var(--space-4)" }}>这部分界面崩溃了，重试即可恢复；若反复出现请重启应用。</p>
            <Button onClick={this.retry} variant="secondary">
              重试
            </Button>
          </ErrorState>
        </div>
      );
    }
    return this.props.children;
  }
}
