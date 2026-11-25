import { Link } from "react-router-dom";
import SearchForm from "../components/SearchForm";
import { dhakaDate } from "../format";

const POPULAR = [
  ["dhaka", "chattogram", "Dhaka", "Chattogram"],
  ["chattogram", "coxs-bazar", "Chattogram", "Cox's Bazar"],
  ["dhaka", "sylhet", "Dhaka", "Sylhet"],
  ["dhaka", "coxs-bazar", "Dhaka", "Cox's Bazar"],
  ["dhaka", "khulna", "Dhaka", "Khulna"],
];

export default function Home() {
  const tomorrow = dhakaDate(1);
  return (
    <>
      <section className="hero">
        <p className="eyebrow">Intercity buses · Bangladesh</p>
        <h1>Pick your seat. <span className="hero-bn">আসন বেছে নিন।</span></h1>
        <p className="lede">
          See every seat live as others book. The seats you pick are yours for five minutes
          while you pay, and nobody else can take them.
        </p>
        <SearchForm />
      </section>
      <section className="popular" aria-labelledby="popular-h">
        <h2 id="popular-h">Popular tomorrow</h2>
        <ul>
          {POPULAR.map(([f, t, fn, tn]) => (
            <li key={f + t}>
              <Link to={`/search?from=${f}&to=${t}&date=${tomorrow}`}>{fn} <span aria-hidden="true">→</span> {tn}</Link>
            </li>
          ))}
        </ul>
      </section>
      <section className="how" aria-labelledby="how-h">
        <h2 id="how-h">How it works</h2>
        <ol>
          <li><strong>Choose seats</strong> on a live map. Seats other people are holding show up as you watch.</li>
          <li><strong>Pay within five minutes.</strong> Your seats are locked to you while you pay.</li>
          <li><strong>Get your ticket</strong> with a PNR. If a payment ever goes through too late, it is refunded automatically.</li>
        </ol>
      </section>
    </>
  );
}
