import { useCallback, useEffect, useState } from "react";

import type {
  CustomProviderKind,
  CustomProviderModel,
  CustomProviderModelDiscoveryResponse,
  CustomProviderModelListRequest,
  ProviderAuthMode,
  ProviderAuthOperation,
} from "@/lib/types";

export function isCustomProviderKind(kind: string): kind is CustomProviderKind {
  return kind === "custom_openai_compatible" || kind === "custom_anthropic_compatible";
}

export function isAuthTerminal(operation: ProviderAuthOperation): boolean {
  return ["succeeded", "failed", "cancelled", "timed_out", "interrupted"].includes(operation.status);
}

export function firstAuthMode(modes: ProviderAuthMode[] | undefined): ProviderAuthMode {
  if (modes?.includes("api_key")) return "api_key";
  if (modes?.includes("oauth")) return "oauth";
  return modes?.[0] ?? "api_key";
}

type CustomModelDiscover = (
  input: Omit<CustomProviderModelListRequest, "protocol">,
  opts: { signal?: AbortSignal },
) => Promise<CustomProviderModelDiscoveryResponse>;

export function useCustomModelDiscovery(options: {
  baseUrl: string;
  apiKey: string;
  enabled: boolean;
  discover: CustomModelDiscover;
}) {
  const [models, setModels] = useState<CustomProviderModel[]>([]);
  const [state, setState] = useState<"idle" | "loading" | "loaded" | "manual">("idle");
  const [message, setMessage] = useState("");
  const [error, setError] = useState(false);
  const [needsProtocolChoice, setNeedsProtocolChoice] = useState(false);
  const [protocolConfirmed, setProtocolConfirmed] = useState(false);
  const [kind, setKind] = useState<CustomProviderKind>("custom_openai_compatible");

  useEffect(() => {
    if (!options.enabled || !options.baseUrl.trim() || !options.apiKey.trim()) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setState("loading");
      setMessage("");
      setError(false);
      void options.discover({
        base_url: options.baseUrl.trim(),
        api_key: options.apiKey,
      }, { signal: controller.signal })
        .then((response) => {
          if (controller.signal.aborted) return;
          setKind(response.protocol === "anthropic-messages" ? "custom_anthropic_compatible" : "custom_openai_compatible");
          setProtocolConfirmed(true);
          setModels(response.models);
          if (response.models.length) {
            setState("loaded");
          } else {
            setState("manual");
            setMessage("这个 Provider 没有返回可选 Model ID，请手动填写。");
          }
        })
        .catch(() => {
          if (controller.signal.aborted) return;
          setModels([]);
          setNeedsProtocolChoice(true);
          setProtocolConfirmed(false);
          setState("manual");
          setMessage("无法自动识别接口协议或读取模型列表，请选择协议后手动填写 Model ID。");
          setError(true);
        });
    }, 500);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [options.baseUrl, options.apiKey, options.enabled, options.discover]);

  const reset = useCallback(() => {
    setModels([]);
    setState("idle");
    setMessage("");
    setError(false);
    setNeedsProtocolChoice(false);
    setProtocolConfirmed(false);
  }, []);

  const confirmProtocol = useCallback((nextKind: CustomProviderKind) => {
    setKind(nextKind);
    setProtocolConfirmed(true);
  }, []);

  const enterManualMode = useCallback(() => {
    setState("manual");
    setMessage("");
  }, []);

  return { models, state, message, error, needsProtocolChoice, protocolConfirmed, kind, reset, confirmProtocol, enterManualMode };
}
