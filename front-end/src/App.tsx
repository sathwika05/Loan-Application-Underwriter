import { useEffect, useState } from "react";
import { Applications } from "./pages/Applications";
import { Dashboard } from "./pages/Dashboard";
import { NewApplication } from "./pages/NewApplication";

type Page = "new" | "applications" | "dashboard";

const NAV: { id: Page; label: string }[] = [
  { id: "new", label: "New Application" },
  { id: "applications", label: "Applications" },
  { id: "dashboard", label: "Dashboard" },
];

interface Route {
  page: Page;
  selectedId: number | null;
}

/** The current view lives in the URL hash (#/dashboard, #/applications/5) so a browser refresh keeps it. */
function parseHash(hash: string): Route {
  const [, page, id] = hash.replace(/^#/, "").split("/");
  if (page === "dashboard") return { page, selectedId: null };
  if (page === "applications") {
    const n = Number(id);
    return { page, selectedId: Number.isInteger(n) && n > 0 ? n : null };
  }
  return { page: "new", selectedId: null };
}

function toHash({ page, selectedId }: Route): string {
  return page === "applications" && selectedId !== null ? `#/applications/${selectedId}` : `#/${page}`;
}

export function App() {
  const [route, setRoute] = useState<Route>(() => parseHash(window.location.hash));
  const { page, selectedId } = route;

  // Back/forward buttons and manual URL edits.
  useEffect(() => {
    const onHash = () => setRoute(parseHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const go = (next: Route) => {
    const hash = toHash(next);
    if (window.location.hash !== hash) window.location.hash = hash; // fires hashchange -> setRoute
    else setRoute(next);
  };

  const openApplication = (id: number) => go({ page: "applications", selectedId: id });

  return (
    <>
      <header className="topbar">
        <strong>Underwriter Routing</strong>
        <nav aria-label="Main">
          {NAV.map((n) => (
            <button
              key={n.id}
              type="button"
              className={page === n.id ? "nav active" : "nav"}
              aria-current={page === n.id ? "page" : undefined}
              onClick={() => go({ page: n.id, selectedId: null })}
            >
              {n.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {page === "new" && <NewApplication onOpenApplication={openApplication} />}
        {page === "applications" && (
          <Applications selectedId={selectedId} onSelect={(id) => go({ page: "applications", selectedId: id })} />
        )}
        {page === "dashboard" && <Dashboard onOpenApplication={openApplication} />}
      </main>
    </>
  );
}
