import { Link, NavLink, Route, Routes } from "react-router-dom";
import ThemeToggle from "./components/ThemeToggle";
import BookingStatus from "./pages/BookingStatus";
import Home from "./pages/Home";
import NotFound from "./pages/NotFound";
import Results from "./pages/Results";
import Ticket from "./pages/Ticket";
import TripPage from "./pages/TripPage";

export default function App() {
  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="brand" aria-label="Bus Ticket Booking home">
          <span className="brand-mark" aria-hidden="true">B</span>
          <span className="brand-name">Bus Ticket Booking</span>
        </Link>
        <nav className="topnav">
          <NavLink to="/" end>Find buses</NavLink>
          <NavLink to="/ticket">My ticket</NavLink>
          <ThemeToggle />
        </nav>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/search" element={<Results />} />
          <Route path="/trip/:tripId" element={<TripPage />} />
          <Route path="/booking/:holdId" element={<BookingStatus />} />
          <Route path="/ticket" element={<Ticket />} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer className="footer">
        Demo project. Operators are fictional and payments go through a sandbox provider.
      </footer>
    </div>
  );
}
