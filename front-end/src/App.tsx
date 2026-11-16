import { useState } from "react";
import { Applications } from "./pages/Applications";
import { Dashboard } from "./pages/Dashboard";
import { NewApplication } from "./pages/NewApplication";

type Page = "new" | "applications" | "dashboard";

const NAV: { id: Page; label: string }[] = [
  { id: "new", label: "New Application" },
  { id: "applications", label: "Applications" },
  { id: "dashboard", label: "Dashboard" },
];

export function App() {
  const [page, setPage] = useState<Page>("new");
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const openApplication = (id: number) => {
    setSelectedId(id);
    setPage("applications");
  };

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
              onClick={() => {
                setPage(n.id);
                if (n.id === "applications") setSelectedId(null);
              }}
            >
              {n.label}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {page === "new" && <NewApplication onOpenApplication={openApplication} />}
        {page === "applications" && <Applications selectedId={selectedId} onSelect={setSelectedId} />}
        {page === "dashboard" && <Dashboard onOpenApplication={openApplication} />}
      </main>
    </>
  );
}
