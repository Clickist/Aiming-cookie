"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { getProductState } from "@/lib/api";
import { getProductStartRoute } from "@/lib/contracts";
import { Button, ErrorState, Loading } from "@/ui/primitives";

export default function StartupPage() {
  const router = useRouter();
  const [error, setError] = useState(false);

  const resolveStart = useCallback(async () => {
    setError(false);
    try {
      const state = await getProductState();
      const route = getProductStartRoute(state);
      if (!route) {
        setError(true);
        return;
      }
      router.replace(route);
    } catch {
      setError(true);
    }
  }, [router]);

  useEffect(() => {
    void resolveStart();
  }, [resolveStart]);

  return (
    <main className="task3-startup" id="main-content">
      {error ? (
        <ErrorState title="暂时无法读取本地产品状态">
          <p>没有把读取失败当成空数据。请确认本地服务可用后重试。</p>
          <Button onClick={() => void resolveStart()} variant="secondary">重试</Button>
        </ErrorState>
      ) : (
        <Loading>正在恢复本地工作区</Loading>
      )}
    </main>
  );
}
