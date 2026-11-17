import { Suspense, lazy, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Navigate, Route, BrowserRouter as Router, Routes } from "react-router-dom";

import { AkmsGate } from "./components/AkmsGate";
import { FailureMemoryGate } from "./components/FailureMemoryGate";
import { MechdslGate } from "./components/MechdslGate";
import { RecordsGate } from "./components/RecordsGate";
import { AppShell } from "./components/AppShell";
import { LearnGate } from "./components/LearnGate";
import type { Mode } from "./components/TopModeNav";
import { FirstRunDialog, useFirstRunGate } from "./components/FirstRunDialog";
import HomeRoute from "./routes/home";

const GraphRoute = lazy(() => import("./routes/akms/graph"));
const LearnRoute = lazy(() => import("./routes/akms/learn"));
const NodesRoute = lazy(() => import("./routes/akms/nodes"));
const BatchesRoute = lazy(() => import("./routes/akms/batches"));
const PickerRoute = lazy(() => import("./routes/akms/picker"));
const CollectionsRoute = lazy(() => import("./routes/zsum/collections"));
const OverviewRoute = lazy(() => import("./routes/zsum/overview"));
const PipelineRoute = lazy(() => import("./routes/zsum/pipeline"));
const PromptsRoute = lazy(() => import("./routes/zsum/prompts"));
const RunsRoute = lazy(() => import("./routes/zsum/runs"));
const SettingsRoute = lazy(() => import("./routes/zsum/settings"));
const VaultRoute = lazy(() => import("./routes/zsum/vault"));
const AkmsOverviewRoute = lazy(() => import("./routes/akms/overview"));
const AkmsPromptsRoute = lazy(() => import("./routes/akms/prompts"));
const AkmsRunsRoute = lazy(() => import("./routes/akms/runs"));
const AkmsSettingsRoute = lazy(() => import("./routes/akms/settings"));
const AkmsWorkflowsRoute = lazy(() => import("./routes/akms/workflows"));
const AssistantOverviewRoute = lazy(() => import("./routes/assistant/overview"));
const AssistantDiscoverRoute = lazy(() => import("./routes/assistant/discover"));
const AssistantBatchesRoute = lazy(() => import("./routes/assistant/batches"));
const AssistantWorkflowsRoute = lazy(() => import("./routes/assistant/workflows"));
const AssistantOutputsRoute = lazy(() => import("./routes/assistant/outputs"));
const AssistantPromptsRoute = lazy(() => import("./routes/assistant/prompts"));
const AssistantRunsRoute = lazy(() => import("./routes/assistant/runs"));
const AssistantSettingsRoute = lazy(() => import("./routes/assistant/settings"));
const MechdslOverviewRoute = lazy(() => import("./routes/mechdsl/overview"));
const MechdslCompileRoute = lazy(() => import("./routes/mechdsl/compile"));
const MechdslArtifactsRoute = lazy(() => import("./routes/mechdsl/artifacts"));
const FailureMemoryOverviewRoute = lazy(() => import("./routes/failure-memory/overview"));
const FailureMemoryLessonsRoute = lazy(() => import("./routes/failure-memory/lessons"));
const FailureMemoryRoutesRoute = lazy(() => import("./routes/failure-memory/routes"));
const FailureMemoryPreviewRoute = lazy(() => import("./routes/failure-memory/preview"));
const FailureMemoryGraphRoute = lazy(() => import("./routes/failure-memory/graph"));
const FailureMemoryOperateRoute = lazy(() => import("./routes/failure-memory/operate"));
const RecordsOverviewRoute = lazy(() => import("./routes/records/overview"));
const RecordsBrowseRoute = lazy(() => import("./routes/records/browse"));
const RecordsSchemasRoute = lazy(() => import("./routes/records/schemas"));
const RecordsGraphRoute = lazy(() => import("./routes/records/graph"));
const UnifiedSettingsRoute = lazy(() => import("./routes/settings"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

function RouteFallback() {
  return (
    <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
      Loading view…
    </div>
  );
}

function LazyRoute({ children }: { children: ReactNode }) {
  return <Suspense fallback={<RouteFallback />}>{children}</Suspense>;
}

function Shell() {
  const { status, loading, refresh } = useFirstRunGate();
  // currentMode tracks the active mode for future Shell-level use (e.g. a GlobalRunStatus filter).
  // The setter is passed to AppShell so mode-switch navigation updates shared state.
  const [, setCurrentMode] = useState<Mode>("zsum");

  if (loading) {
    return (
      <div className="flex h-screen items-center justify-center text-sm text-muted-foreground">
        Connecting to Logic-Loom API…
      </div>
    );
  }

  return (
    <Router>
      <Routes>
        {/* ── Root home page — Logic-Loom landing ── */}
        <Route path="/" element={<HomeRoute />} />
        <Route path="/home" element={<Navigate to="/" replace />} />
        <Route path="/settings" element={<LazyRoute><UnifiedSettingsRoute /></LazyRoute>} />

        {/* ── Legacy redirects — keep all old deep links alive ── */}
        <Route path="/picker" element={<Navigate to="/akms/picker" replace />} />
        <Route path="/nodes" element={<Navigate to="/akms/nodes" replace />} />
        <Route path="/graph" element={<Navigate to="/akms/graph" replace />} />
        <Route path="/pipeline" element={<Navigate to="/zsum/pipeline" replace />} />
        <Route path="/vault" element={<Navigate to="/zsum/vault" replace />} />
        <Route path="/vault/*" element={<Navigate to="/zsum/vault" replace />} />

        {/* ── Zsum mode (/zsum/*) ── */}
        <Route
          path="/zsum/*"
          element={
            <AppShell currentMode="zsum" onModeChange={setCurrentMode} />
          }
        >
          <Route index element={<LazyRoute><OverviewRoute /></LazyRoute>} />
          <Route path="overview" element={<LazyRoute><OverviewRoute /></LazyRoute>} />
          <Route path="pipeline" element={<LazyRoute><PipelineRoute /></LazyRoute>} />
          <Route path="vault/*" element={<LazyRoute><VaultRoute /></LazyRoute>} />
          <Route path="collections" element={<LazyRoute><CollectionsRoute /></LazyRoute>} />
          <Route path="prompts" element={<LazyRoute><PromptsRoute /></LazyRoute>} />
          <Route path="runs" element={<LazyRoute><RunsRoute /></LazyRoute>} />
          <Route path="settings" element={<LazyRoute><SettingsRoute /></LazyRoute>} />
          <Route path="*" element={<Navigate to="/zsum" replace />} />
        </Route>

        {/* ── AKMS mode (/akms/*) — wrapped in AkmsGate ── */}
        <Route
          path="/akms/*"
          element={
            <AppShell currentMode="akms" onModeChange={setCurrentMode} gate={AkmsGate} />
          }
        >
          <Route index element={<LazyRoute><AkmsOverviewRoute /></LazyRoute>} />
          <Route path="overview" element={<LazyRoute><AkmsOverviewRoute /></LazyRoute>} />
          <Route path="batches" element={<LazyRoute><BatchesRoute /></LazyRoute>} />
          <Route path="picker" element={<LazyRoute><PickerRoute /></LazyRoute>} />
          <Route path="nodes" element={<LazyRoute><NodesRoute /></LazyRoute>} />
          <Route path="graph" element={<LazyRoute><AkmsGate feature="graph"><GraphRoute /></AkmsGate></LazyRoute>} />
          <Route path="learn" element={<LazyRoute><LearnGate><LearnRoute /></LearnGate></LazyRoute>} />
          <Route path="prompts" element={<LazyRoute><AkmsPromptsRoute /></LazyRoute>} />
          <Route path="workflows" element={<LazyRoute><AkmsWorkflowsRoute /></LazyRoute>} />
          <Route path="runs" element={<LazyRoute><AkmsRunsRoute /></LazyRoute>} />
          <Route path="settings" element={<LazyRoute><AkmsSettingsRoute /></LazyRoute>} />
          <Route path="*" element={<Navigate to="/akms" replace />} />
        </Route>

        {/* ── Assistant mode (/assistant/*) ── */}
        <Route
          path="/assistant/*"
          element={
            <AppShell currentMode="assistant" onModeChange={setCurrentMode} />
          }
        >
          <Route
            index
            element={<LazyRoute><AssistantOverviewRoute /></LazyRoute>}
          />
          <Route path="discover" element={<LazyRoute><AssistantDiscoverRoute /></LazyRoute>} />
          <Route path="batches" element={<LazyRoute><AssistantBatchesRoute /></LazyRoute>} />
          <Route path="workflows" element={<LazyRoute><AssistantWorkflowsRoute /></LazyRoute>} />
          <Route path="outputs" element={<LazyRoute><AssistantOutputsRoute /></LazyRoute>} />
          <Route path="prompts" element={<LazyRoute><AssistantPromptsRoute /></LazyRoute>} />
          <Route path="runs" element={<LazyRoute><AssistantRunsRoute /></LazyRoute>} />
          <Route path="settings" element={<LazyRoute><AssistantSettingsRoute /></LazyRoute>} />
          <Route path="*" element={<Navigate to="/assistant" replace />} />
        </Route>

        {/* ── MechDSL mode (/mechdsl/*) — wrapped in MechdslGate ── */}
        <Route
          path="/mechdsl/*"
          element={
            <AppShell currentMode="mechdsl" onModeChange={setCurrentMode} gate={MechdslGate} />
          }
        >
          <Route index element={<LazyRoute><MechdslOverviewRoute /></LazyRoute>} />
          <Route path="overview" element={<LazyRoute><MechdslOverviewRoute /></LazyRoute>} />
          <Route path="compile" element={<LazyRoute><MechdslCompileRoute /></LazyRoute>} />
          <Route path="artifacts" element={<LazyRoute><MechdslArtifactsRoute /></LazyRoute>} />
          <Route path="*" element={<Navigate to="/mechdsl" replace />} />
        </Route>

        {/* ── Failure Memory mode (/failure-memory/*) — wrapped in FailureMemoryGate ──
             Read-only workspace over explicitly configured failure-memory projects.
             The gate degrades the TAB when
             the optional akms-failure-memory package is absent; the rest of the app
             is unaffected. ── */}
        <Route
          path="/failure-memory/*"
          element={
            <AppShell currentMode="failure-memory" onModeChange={setCurrentMode} gate={FailureMemoryGate} />
          }
        >
          <Route index element={<LazyRoute><FailureMemoryOverviewRoute /></LazyRoute>} />
          <Route path="overview" element={<LazyRoute><FailureMemoryOverviewRoute /></LazyRoute>} />
          <Route path="lessons" element={<LazyRoute><FailureMemoryLessonsRoute /></LazyRoute>} />
          <Route path="routes" element={<LazyRoute><FailureMemoryRoutesRoute /></LazyRoute>} />
          <Route path="preview" element={<LazyRoute><FailureMemoryPreviewRoute /></LazyRoute>} />
          <Route path="graph" element={<LazyRoute><FailureMemoryGraphRoute /></LazyRoute>} />
          {/* Operator mode. The route is always mounted; the page and
              the backend both refuse unless failure_memory_mode = "operator". */}
          <Route path="record" element={<LazyRoute><FailureMemoryOperateRoute /></LazyRoute>} />
          <Route path="*" element={<Navigate to="/failure-memory" replace />} />
        </Route>

        {/* ── Records mode (/records/*) — wrapped in RecordsGate ──
             Read-only browse of explicitly configured akms-records stores. ── */}
        <Route
          path="/records/*"
          element={
            <AppShell currentMode="records" onModeChange={setCurrentMode} gate={RecordsGate} />
          }
        >
          <Route index element={<LazyRoute><RecordsOverviewRoute /></LazyRoute>} />
          <Route path="overview" element={<LazyRoute><RecordsOverviewRoute /></LazyRoute>} />
          <Route path="browse" element={<LazyRoute><RecordsBrowseRoute /></LazyRoute>} />
          <Route path="schemas" element={<LazyRoute><RecordsSchemasRoute /></LazyRoute>} />
          <Route path="graph" element={<LazyRoute><RecordsGraphRoute /></LazyRoute>} />
          <Route path="*" element={<Navigate to="/records" replace />} />
        </Route>

        {/* ── Catch-all → landing ── */}
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>

      {status?.needs_setup && (
        <FirstRunDialog status={status} onComplete={refresh} />
      )}
    </Router>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Shell />
    </QueryClientProvider>
  );
}
