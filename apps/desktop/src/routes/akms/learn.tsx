/**
 * Learn route (`/akms/learn`).
 *
 * Already wrapped in `<LearnGate>` (App.tsx), so by the time this renders the
 * akms-learn plugin is available. We still read `useLearnStatus()` here to feed
 * the status card / capability-derived selectors inside <LearnTab>.
 */
import { useLearnStatus } from "../../hooks/app";
import { LearnTab } from "../learn/LearnTab";

export default function LearnRoute() {
  const { data: status } = useLearnStatus();

  // LearnGate guarantees availability before this mounts; render a brief
  // placeholder only for the (cache-cold) window before the query settles.
  if (!status?.akms_learn_available) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Loading Learn…
      </div>
    );
  }

  return <LearnTab status={status} />;
}
