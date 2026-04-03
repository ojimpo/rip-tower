import { BrowserRouter, Routes, Route } from "react-router-dom";
import Dashboard from "./pages/Dashboard";
import JobDetail from "./pages/JobDetail";
import History from "./pages/History";
import Settings from "./pages/Settings";
import Import from "./pages/Import";
import BottomNav from "./components/BottomNav";

export default function App() {
  return (
    <BrowserRouter>
      <div className="max-w-md md:max-w-2xl lg:max-w-3xl mx-auto min-h-screen" style={{ paddingTop: "env(safe-area-inset-top, 0px)", paddingBottom: "calc(5rem + env(safe-area-inset-bottom, 0px))" }}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/job/:id" element={<JobDetail />} />
          <Route path="/import" element={<Import />} />
          <Route path="/history" element={<History />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
        <BottomNav />
      </div>
    </BrowserRouter>
  );
}
