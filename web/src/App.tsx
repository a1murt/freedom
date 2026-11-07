import * as React from "react";
import { HashRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { TooltipProvider } from "@/components/ui/misc";
import { UiProvider, useUi } from "@/store/ui";
import { AppShell } from "@/components/layout/AppShell";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { embedConfig } from "@/lib/config";
import { ApiError } from "@/api/client";
import ConversationsPage from "@/features/conversations/ConversationsPage";
import ConversationCardPage from "@/features/card/ConversationCardPage";
import ChecklistsPage from "@/features/checklists/ChecklistsPage";
import ChecklistEditorPage from "@/features/checklists/ChecklistEditorPage";
import ReviewPage from "@/features/review/ReviewPage";
import AnalyticsPage from "@/features/analytics/AnalyticsPage";
import SettingsPage from "@/features/settings/SettingsPage";
import EmbedPage from "@/features/card/EmbedPage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, err) => {
        if (err instanceof ApiError && err.status >= 400 && err.status < 500) return false;
        return count < 1;
      },
      refetchOnWindowFocus: false,
      staleTime: 10_000,
    },
  },
});

function ToasterThemed() {
  const { theme } = useUi();
  return <Toaster theme={theme} position="bottom-right" closeButton richColors={false} toastOptions={{ className: "!rounded-md !border !border-line !bg-surface !text-text !shadow-float", descriptionClassName: "!text-text-2" }} />;
}

function Router() {
  const { embed, conversation } = React.useMemo(() => embedConfig(), []);
  if (embed) return <EmbedPage conversationId={conversation} />;
  return (
    <HashRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/conversations" replace />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/conversations/:id" element={<ConversationCardPage />} />
          <Route path="/checklists" element={<ChecklistsPage />} />
          <Route path="/checklists/:id/versions/:vid" element={<ChecklistEditorPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/analytics" element={<AnalyticsPage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/conversations" replace />} />
        </Route>
      </Routes>
    </HashRouter>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <UiProvider>
        <TooltipProvider delayDuration={300}>
          <ErrorBoundary>
            <Router />
          </ErrorBoundary>
          <ToasterThemed />
        </TooltipProvider>
      </UiProvider>
    </QueryClientProvider>
  );
}
