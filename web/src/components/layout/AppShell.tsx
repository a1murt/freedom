import * as React from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { BarChart3, ChevronsLeft, ChevronsRight, ListChecks, MessagesSquare, Moon, Settings, ShieldCheck, Sun, Wifi, WifiOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useUi } from "@/store/ui";
import { LANGS, setLang, type Lang } from "@/i18n";
import { useHealth, useSettings } from "@/api/queries";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/ui/misc";
import { MOCK } from "@/lib/config";
import { Badge } from "@/components/ui/badge";
import { Mascot } from "@/components/Mascot";
import { ErrorBoundary } from "@/components/ErrorBoundary";

const NAV = [
  { to: "/conversations", key: "nav.conversations", icon: MessagesSquare },
  { to: "/checklists", key: "nav.checklists", icon: ListChecks },
  { to: "/review", key: "nav.review", icon: ShieldCheck },
  { to: "/analytics", key: "nav.analytics", icon: BarChart3 },
  { to: "/settings", key: "nav.settings", icon: Settings },
] as const;

function Wordmark({ collapsed }: { collapsed: boolean }) {
  return (
    <div className={cn("flex h-14 items-center gap-2 px-4", collapsed && "justify-center px-0")}>
      <Mascot pose="wave" size={28} decorative className="shrink-0" />
      {!collapsed ? <span className="inline-block size-2 rounded-full bg-accent shadow-[0_0_12px_rgb(var(--accent)/0.7)]" /> : null}
      {!collapsed ? (
        <span className="font-display text-[17px] font-semibold tracking-tight">
          CQA <span className="font-normal text-text-2">by Freedom</span>
        </span>
      ) : null}
    </div>
  );
}

export function SideNav() {
  const { t } = useTranslation();
  const { navCollapsed, setNavCollapsed } = useUi();
  return (
    <aside className={cn("flex h-full shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-200", navCollapsed ? "w-14" : "w-56")}>
      <Wordmark collapsed={navCollapsed} />
      <nav className="flex flex-1 flex-col gap-0.5 px-2">
        {NAV.map((n) => (
          <Hint key={n.to} label={navCollapsed ? t(n.key) : undefined} side="right">
            <NavLink
              to={n.to}
              className={({ isActive }) =>
                cn(
                  "flex h-9 items-center gap-2.5 rounded-sm px-2.5 text-caption font-medium text-text-2 transition-colors hover:bg-surface-2 hover:text-text",
                  isActive && "bg-tint text-accent hover:bg-tint hover:text-accent",
                  navCollapsed && "justify-center px-0",
                )
              }
            >
              <n.icon className="size-4 shrink-0" />
              {!navCollapsed ? <span className="truncate">{t(n.key)}</span> : null}
            </NavLink>
          </Hint>
        ))}
      </nav>
      <div className="px-2 py-2">
        <Button variant="ghost" size={navCollapsed ? "icon" : "md"} className={cn("w-full justify-start text-text-2", navCollapsed && "justify-center")} onClick={() => setNavCollapsed(!navCollapsed)} title={navCollapsed ? t("nav.expand") : t("nav.collapse")}>
          {navCollapsed ? <ChevronsRight /> : <ChevronsLeft />}
          {!navCollapsed ? <span>{t("nav.collapse")}</span> : null}
        </Button>
      </div>
    </aside>
  );
}

export function TopBar() {
  const { t, i18n } = useTranslation();
  const { role, setRole, theme, setTheme } = useUi();
  const settings = useSettings();
  const health = useHealth();
  const provider = settings.data?.default_provider;
  const model = provider === "local" ? settings.data?.local_model : settings.data?.default_model;
  const online = !!health.data?.ok;

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b border-line bg-surface px-4">
      <div className="flex items-center gap-2">
        <div className="inline-flex h-8 items-center rounded-sm border border-line bg-surface-2 p-0.5">
          {(["analyst", "reviewer"] as const).map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRole(r)}
              className={cn("h-full rounded-[4px] px-2.5 text-caption font-medium text-text-2 transition-colors", role === r && "bg-surface text-text shadow-sm")}
            >
              {t(`role.${r}`)}
            </button>
          ))}
        </div>
        {MOCK ? <Badge variant="warning">mock</Badge> : null}
      </div>
      <div className="flex items-center gap-2">
        <Hint label={online ? `${t("settings.api")}: ${t("settings.ok")}` : t("provider.offline")}>
          <div className={cn("inline-flex h-8 items-center gap-1.5 rounded-sm border border-line px-2.5 text-caption", online ? "text-text" : "text-critical")}>
            {online ? <Wifi className="size-3.5 text-success" /> : <WifiOff className="size-3.5" />}
            {provider ? (
              <span>
                {t(`provider.${provider}` as never, { defaultValue: provider })}
                {model ? <span className="font-mono text-[11px] text-text-2"> · {String(model).replace(/^anthropic:/, "")}</span> : null}
              </span>
            ) : (
              <span className="text-text-2">{t("provider.badge")}</span>
            )}
          </div>
        </Hint>
        <div className="inline-flex h-8 items-center rounded-sm border border-line bg-surface-2 p-0.5">
          {LANGS.map((l) => (
            <button
              key={l}
              type="button"
              onClick={() => setLang(l as Lang)}
              className={cn("h-full rounded-[4px] px-2 font-mono text-[12px] font-medium text-text-2 transition-colors", i18n.language === l && "bg-surface text-text shadow-sm")}
            >
              {t(`lang.${l}`)}
            </button>
          ))}
        </div>
        <Hint label={t("theme.toggle")}>
          <Button variant="ghost" size="icon" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label={t("theme.toggle")}>
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </Hint>
      </div>
    </header>
  );
}

export function AppShell() {
  const location = useLocation();
  const mainRef = React.useRef<HTMLElement>(null);
  React.useEffect(() => {
    mainRef.current?.scrollTo({ top: 0 });
  }, [location.pathname]);
  return (
    <div className="flex h-full w-full overflow-hidden bg-bg">
      <SideNav />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <main ref={mainRef} className="min-h-0 flex-1 overflow-auto">
          <ErrorBoundary key={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
