import { Link, NavLink, Route, Routes } from "react-router-dom";
import Home from "./pages/Home";
import NotFound from "./pages/NotFound";
import Results from "./pages/Results";

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand" aria-label="Aashon home">
          <span className="brand-mark" aria-hidden="true">আ</span>
          <span className="brand-name">Aashon</span>
        </Link>
        <nav className="topnav">
          <NavLink to="/" end>Find buses</NavLink>
          <NavLink to="/ticket">My ticket</NavLink>
        </nav>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/search" element={<Results />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="footer">
        Demo project. Operators are fictional and payments go through a sandbox provider.
      </footer>
    </div>
  );
}
