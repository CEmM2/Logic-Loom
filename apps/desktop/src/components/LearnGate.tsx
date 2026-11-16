import type { ReactNode } from "react";

import { useLearnStatus } from "../hooks/app";

interface Props {
  children: ReactNode;
}

export function LearnGate({ children }: Props) {
  const { data, isLoading } = useLearnStatus();

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Checking Learn availability…
      </div>
    );
  }

  if (!data?.akms_learn_available) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <div className="text-lg font-semibold">Learn not available</div>
        <p className="max-w-md text-sm text-muted-foreground">
          {data?.reason
            ? data.reason
            : "The akms-learn plugin is not available."}
          <span className="block mt-1">
            Requires the <code className="rounded bg-muted px-1.5 py-0.5 text-xs">akms-learn</code>{" "}
            extra:{" "}
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">uv sync --extra akms</code>.
          </span>
        </p>
      </div>
    );
  }

  return <>{children}</>;
}
