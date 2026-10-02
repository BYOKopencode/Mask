import { Menu, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";

import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "Home" },
  { to: "/requests", label: "Requests" },
  { to: "/docs", label: "Docs" },
];

export const GET_KEY_URL = "/docs#authentication";

export function SiteLayout({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<boolean>(false);
  const location = useLocation();

  useEffect(() => {
    setOpen(false);
    if (!location.hash) window.scrollTo({ top: 0 });
  }, [location.pathname, location.hash]);

  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-black">
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-border bg-background/85 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-4 px-4 sm:px-6 lg:px-10">
          <Link to="/" className="flex items-center gap-2.5 text-[17px] font-bold tracking-tight" aria-label="Rev9 Apis home">
            <img src="/logo.png" alt="" className="h-7 w-auto rounded" />
            Rev9 Apis
          </Link>
          <nav aria-label="Primary" className="absolute left-1/2 hidden -translate-x-1/2 items-center gap-1 md:flex">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  cn(
                    "rounded-lg px-4 py-2 text-sm font-medium transition",
                    isActive ? "bg-raised text-foreground" : "text-muted-foreground hover:text-foreground",
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-2">
            <Link to={GET_KEY_URL} className="btn-primary hidden h-9 sm:inline-flex">
              Get API key
            </Link>
            <button
              type="button"
              className="inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border md:hidden"
              aria-label={open ? "Close menu" : "Open menu"}
              aria-expanded={open}
              onClick={() => setOpen((value) => !value)}
            >
              {open ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>
          </div>
        </div>
        {open && (
          <nav aria-label="Mobile" className="border-t border-border px-4 py-3 md:hidden">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  cn("block rounded-lg px-3 py-2.5 text-sm font-medium", isActive ? "bg-raised" : "text-muted-foreground")
                }
              >
                {item.label}
              </NavLink>
            ))}
            <Link to={GET_KEY_URL} className="btn-primary mt-2 w-full">
              Get API key
            </Link>
          </nav>
        )}
      </header>
      <main id="main" className="flex-1">
        {children}
      </main>
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1400px] flex-col gap-3 px-4 py-8 text-sm text-muted-foreground sm:flex-row sm:items-center sm:px-6 lg:px-10">
          <span className="font-semibold text-foreground">Rev9 Apis</span>
          <span>One endpoint. Every model. Per-token billing.</span>
          <nav aria-label="Footer" className="flex gap-5 sm:ml-auto">
            <Link to="/requests" className="hover:text-foreground">Requests</Link>
            <Link to="/docs" className="hover:text-foreground">Docs</Link>
            <Link to="/docs#errors" className="hover:text-foreground">Status codes</Link>
          </nav>
        </div>
      </footer>
    </div>
  );
}
