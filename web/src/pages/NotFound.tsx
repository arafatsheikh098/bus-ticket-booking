import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <section className="empty">
      <h1>This page took a wrong turn</h1>
      <p><Link to="/">Back to search</Link></p>
    </section>
  );
}
